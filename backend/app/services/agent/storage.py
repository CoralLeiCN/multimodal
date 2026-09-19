import hashlib
import io
import warnings

from PIL import Image, UnidentifiedImageError

from app.agent_models import Asset, uid


class AgentError(Exception):
    def __init__(self, code, message, status=409):
        self.code, self.message, self.status = code, message, status
        super().__init__(message)


class Storage:
    def __init__(self, settings):
        self.settings = settings
        self.root = settings.asset_root.resolve()
        self.s3 = None
        if settings.storage == "s3":
            import boto3

            self.s3 = boto3.client(
                "s3", endpoint_url=settings.s3_endpoint, region_name=settings.s3_region
            )

    def put(self, key, content, mime):
        if self.s3:
            self.s3.put_object(
                Bucket=self.settings.s3_bucket, Key=key, Body=content, ContentType=mime
            )
        else:
            path = self.root / key
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix(".tmp")
            temporary.write_bytes(content)
            temporary.replace(path)

    def get(self, asset):
        if self.s3:
            body = self.s3.get_object(
                Bucket=self.settings.s3_bucket, Key=asset.object_key
            )["Body"]
            try:
                return body.read(self.settings.max_image_bytes + 1)
            finally:
                body.close()
        path = (self.root / asset.object_key).resolve()
        if not path.is_relative_to(self.root):
            raise AgentError("invalid_asset", "Invalid asset location")
        return path.read_bytes()

    def save(self, data, workspace, kind, source=None):
        if not data or len(data) > self.settings.max_image_bytes:
            raise AgentError("image_too_large", "Images must be at most 10 MB.", 413)
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(io.BytesIO(data)) as image:
                    if (
                        image.format not in {"PNG", "JPEG", "WEBP"}
                        or getattr(image, "n_frames", 1) != 1
                    ):
                        raise AgentError(
                            "unsupported_image",
                            "Use a still PNG, JPEG, or WebP image.",
                            415,
                        )
                    width, height = image.size
                    if width * height > self.settings.max_image_pixels:
                        raise AgentError(
                            "image_too_large", "Image exceeds the pixel limit.", 413
                        )
                    mime = Image.MIME[image.format]
                    image.verify()
        except (
            UnidentifiedImageError,
            OSError,
            ValueError,
            Image.DecompressionBombWarning,
            Image.DecompressionBombError,
        ):
            raise AgentError(
                "invalid_image", "The image could not be decoded.", 422
            ) from None
        asset_id = uid()
        # Workspace IDs never become filesystem paths.
        prefix = hashlib.sha256(workspace.encode()).hexdigest()[:24]
        key = f"{prefix}/{asset_id}"
        self.put(key, data, mime)
        return Asset(
            id=asset_id,
            workspace=workspace,
            kind=kind,
            object_key=key,
            checksum=hashlib.sha256(data).hexdigest(),
            mime=mime,
            width=width,
            height=height,
            source=source or {"type": "user_upload"},
        )

    def prune_orphans(self, workspace, known_keys, older_than):
        """Remove only stale, unreferenced objects in this workspace's generated namespace."""
        import re

        prefix = hashlib.sha256(workspace.encode()).hexdigest()[:24] + "/"
        pattern = re.compile(r"^[0-9a-f]{24}/[0-9a-f]{32}(?:\.tmp)?$")
        removed = 0
        if self.s3:
            paginator = self.s3.get_paginator("list_objects_v2")
            for page in paginator.paginate(
                Bucket=self.settings.s3_bucket,
                Prefix=prefix,
                PaginationConfig={"MaxItems": 1000},
            ):
                for item in page.get("Contents", []):
                    key = item["Key"]
                    if (
                        pattern.fullmatch(key)
                        and key not in known_keys
                        and item["LastModified"].timestamp() < older_than
                    ):
                        self.s3.delete_object(Bucket=self.settings.s3_bucket, Key=key)
                        removed += 1
        else:
            directory = self.root / prefix
            if directory.is_dir():
                for path in directory.iterdir():
                    key = prefix + path.name
                    if (
                        path.is_file()
                        and not path.is_symlink()
                        and pattern.fullmatch(key)
                        and key not in known_keys
                        and path.stat().st_mtime < older_than
                    ):
                        path.unlink()
                        removed += 1
        return removed
