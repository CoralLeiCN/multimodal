import csv
import hashlib

import pandas as pd
import pytest
import silver_to_gold


@pytest.fixture
def silver_csv(tmp_path):
    path = tmp_path / "silver" / "objects.csv"
    path.parent.mkdir()
    return path


@pytest.fixture
def images_dir(tmp_path):
    directory = tmp_path / "images"
    image = directory / "extracted/40/681/a.jpg"
    image.parent.mkdir(parents=True)
    image.write_bytes(b"image fixture")
    return directory


def test_conversion_filters_missing_images_and_preserves_values(silver_csv, images_dir, tmp_path):
    columns = ["uid", "identifier", "date", "description", "empty", "image"]
    rows = [
        ["co001", "000123", "1932", 'Café, quoted "text"\nand a second line', "", "40/681/a.jpg"],
        ["co002", "NA", "1970-1990", "NULL", "", "40/681/a.jpg"],
        ["co002", "001.50", "circa 1900", "  retain spaces  ", "", "40/681/a.jpg"],
        ["", "", "", "", "", "40/681/a.jpg"],
    ]
    rows.append(rows[0].copy())  # Duplicate rows must survive conversion.
    with silver_csv.open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.writer(stream)
        writer.writerow(columns)
        writer.writerows(rows)
        writer.writerow(["missing", "", "", "", "", "40/682/a.jpg"])
        writer.writerow(["no_image", "", "", "", "", ""])
    original = silver_csv.read_bytes()
    output = tmp_path / "gold" / "objects.parquet"
    report = silver_to_gold.convert_csv_to_parquet(silver_csv, output, images_dir=images_dir)
    frame = pd.read_parquet(output, engine="pyarrow")
    assert frame.columns.tolist() == columns
    assert frame.to_dict("records") == [dict(zip(columns, row)) for row in rows]
    assert report["rows"] == len(rows)
    assert report["source_rows"] == len(rows) + 2
    assert report["dropped_rows"] == frame.attrs["dropped_rows"] == 2
    assert frame.attrs["source_sha256"] == hashlib.sha256(original).hexdigest()
    assert silver_csv.read_bytes() == original


@pytest.mark.parametrize("contents", [
    "uid,uid\na,b\n", "uid,\na,b\n", "uid,title\nco1,Title,extra\n", "uid,title\nco1\n",
    "uid,title\nco1,Title\n",
])
def test_invalid_input_preserves_existing_gold(silver_csv, tmp_path, contents):
    silver_csv.write_text(contents, encoding="utf-8")
    output = tmp_path / "gold.parquet"
    pd.DataFrame({"uid": ["previous"]}).to_parquet(output, engine="pyarrow", index=False)
    original = output.read_bytes()
    with pytest.raises(ValueError):
        silver_to_gold.convert_csv_to_parquet(silver_csv, output)
    assert output.read_bytes() == original
    assert not list(tmp_path.glob(".*.tmp"))


def test_filtering_all_rows_keeps_all_columns(silver_csv, images_dir, tmp_path):
    silver_csv.write_text("uid,title,image\nco1,Title,missing.jpg\n", encoding="utf-8")
    output = tmp_path / "empty.parquet"
    report = silver_to_gold.convert_csv_to_parquet(silver_csv, output, images_dir=images_dir)
    frame = pd.read_parquet(output, engine="pyarrow")
    assert report["rows"] == len(frame) == 0
    assert report["dropped_rows"] == 1
    assert frame.columns.tolist() == ["uid", "title", "image"]
    assert all(dtype == pd.StringDtype(storage="pyarrow") for dtype in frame.dtypes)


def test_failed_write_preserves_existing_gold(silver_csv, images_dir, tmp_path, monkeypatch):
    silver_csv.write_text("uid,title,image\nco1,An object,40/681/a.jpg\n", encoding="utf-8")
    output = tmp_path / "gold.parquet"
    pd.DataFrame({"uid": ["previous"]}).to_parquet(output, engine="pyarrow", index=False)
    original = output.read_bytes()

    def fail_write(frame, path, **kwargs):
        path.write_bytes(b"incomplete parquet")
        raise OSError("Write failed")

    monkeypatch.setattr(pd.DataFrame, "to_parquet", fail_write)
    with pytest.raises(OSError, match="Write failed"):
        silver_to_gold.convert_csv_to_parquet(silver_csv, output, images_dir=images_dir)
    assert output.read_bytes() == original
    assert not list(tmp_path.glob(".*.tmp"))


def test_cli_defaults_work_from_another_directory(tmp_path, monkeypatch):
    project = tmp_path / "project"
    silver = project / "data/silver" / silver_to_gold.DEFAULT_CSV
    silver.parent.mkdir(parents=True)
    silver.write_text("uid,title,image\nco1,An object,photo.jpg\n", encoding="utf-8")
    images = project / "data/images"
    images.mkdir()
    (images / "photo.jpg").write_bytes(b"image fixture")
    monkeypatch.setattr(silver_to_gold, "PROJECT_ROOT", project)
    monkeypatch.chdir(tmp_path)
    assert silver_to_gold.main([]) == 0
    output = project / "data/gold/object_records.parquet"
    assert pd.read_parquet(output).to_dict("records") == [
        {"uid": "co1", "title": "An object", "image": "photo.jpg"},
    ]
    assert silver_to_gold.main(["--input", str(tmp_path / "absent.csv")]) == 2
