import csv
import json
import sys

import match_images
import pytest


def test_streaming_handles_chunk_boundaries_and_rejects_invalid_arrays(tmp_path):
    path = tmp_path / "records.json"
    records = [
        {"title": 'Unicode café and escaped "quotes"', "nested": [1, {"x": "}"}]},
        {},
    ]
    path.write_text(json.dumps(records, ensure_ascii=False), encoding="utf-8")
    for size in (1, 7, 64):
        assert list(match_images.iter_records(path, size)) == records
    for invalid in ('[{"x": 1}', "[{},]", "[{}] {}", "{}", "[1]", ""):
        path.write_text(invalid, encoding="utf-8")
        with pytest.raises(ValueError):
            list(match_images.iter_records(path, 2))
    path.write_text("[] \n", encoding="utf-8")
    assert list(match_images.iter_records(path, 1)) == []


def test_full_path_matching_and_ambiguity():
    index = {"photo.jpg": ["wrapper/40/681/photo.jpg", "wrapper/40/682/photo.jpg"]}
    status, relative, paths = match_images.match_location("/40/681/photo.jpg", index)
    assert (status, relative, paths) == (
        "matched",
        "40/681/photo.jpg",
        ["wrapper/40/681/photo.jpg"],
    )
    assert match_images.match_location("99/681/photo.jpg", index)[0] == "missing"
    assert (
        match_images.match_location("https://example.org/40/681/photo.jpg", index)[0]
        == "matched"
    )
    index["photo.jpg"].append("another/40/681/photo.jpg")
    assert match_images.match_location("40/681/photo.jpg", index)[0] == "ambiguous"
    assert (
        match_images.match_location("%2e%2e/40/681/photo.jpg", index)[0]
        == "invalid_location"
    )


def test_manifest_counts_shared_missing_and_absent_images(tmp_path):
    images, output = tmp_path / "images", tmp_path / "output"
    existing = images / "extracted/40/681/photo.jpg"
    existing.parent.mkdir(parents=True)
    existing.write_bytes(b"fixture; path matching does not decode images")
    (images / "unreferenced.jpg").write_bytes(b"fixture")

    def media(uid, location, width=192, height=267):
        return {
            "@admin": {"uid": uid},
            "@processed": {
                "medium_thumbnail": {
                    "location": location,
                    "measurements": {
                        "dimensions": [
                            {"dimension": "height", "units": "pixels", "value": height},
                            {"dimension": "width", "units": "pixels", "value": width},
                        ]
                    },
                }
            },
            "legal": {
                "rights": [{"licence": "CC BY-NC-SA 4.0", "copyright": "Museum"}]
            },
            "credit": {"value": "Collection"},
        }

    first = tmp_path / "objects.json"
    first.write_text(
        json.dumps(
            [
                {
                    "@admin": {"uid": "co1"},
                    "title": [
                        {"value": "Other"},
                        {"value": "Primary", "primary": True},
                    ],
                    "description": [
                        {"value": 'Description with commas, quotes " and\nnewlines'}
                    ],
                    "creation": {
                        "date": [{"value": "1932", "from": "1932", "to": "1932"}],
                        "maker": [
                            {"summary": {"title": "John Douglas Cockcroft"}},
                            {"summary": {"title": "Ernest Thomas Sinton Walton"}},
                        ],
                    },
                    "identifier": [
                        {"type": "accession number", "value": "1933-501"},
                        {"type": "file number", "value": "1494/1/1"},
                    ],
                    "category": [{"name": "Nuclear Physics"}],
                    "measurements": {
                        "display": "overall: 4500 x 1720 x 920 mm",
                        "dimensions": [
                            {"dimension": "weight", "value": "250", "units": "kg"}
                        ],
                    },
                    "multimedia": [
                        media("i1", "40/681/photo.jpg"),
                        media("i2", "40/682/missing.jpg", 200, 150),
                        {},
                    ],
                },
                {"@admin": {"uid": "co2"}},
            ]
        ),
        encoding="utf-8",
    )
    second = tmp_path / "documents.json"
    second.write_text(
        json.dumps(
            [
                {
                    "@admin": {"uid": "aa1"},
                    "multimedia": [media("i1", "40/681/photo.jpg")],
                    "creation": {
                        "date": [{"value": "1826-12-05"}],
                        "maker": [
                            {"summary": {"title": "Liverpool & Manchester Railway Co"}}
                        ],
                    },
                    "identifier": [{"value": "CHTMSS"}, {"value": "2017-7050"}],
                    "measurements": {"dimensions": [{"value": "1 item"}]},
                    "level": {"value": "fonds"},
                }
            ]
        ),
        encoding="utf-8",
    )
    summary = match_images.build_manifest([first, second], images, output)
    assert summary["unique_referenced_images"] == 2
    assert summary["unique_matched_images"] == 1
    assert summary["unique_missing_images"] == 1
    assert summary["local_image_files_not_matched"] == 1
    assert summary["totals"]["records"] == 3
    assert summary["totals"]["records_with_matched_images"] == 2
    assert summary["totals"]["records_without_matched_images"] == 1
    assert summary["totals"]["media_entries"] == 4
    assert summary["totals"]["matched"] == 2
    assert summary["totals"]["no_thumbnail"] == 1
    with (output / "image_manifest.csv").open(newline="", encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
    assert len(rows) == 4
    assert rows[0]["title"] == "Primary"
    assert rows[0]["image_path"] == str(existing)
    assert rows[0]["licence"] == "CC BY-NC-SA 4.0"
    assert rows[1]["status"] == "missing"
    assert rows[1]["image_path"] == ""
    assert rows[0]["date"] == "1932"
    assert rows[0]["maker"] == "John Douglas Cockcroft; Ernest Thomas Sinton Walton"
    assert rows[0]["catalogue_identifiers"] == "1933-501; 1494/1/1"
    assert rows[0]["measurements"] == "overall: 4500 x 1720 x 920 mm; weight: 250 kg"
    assert rows[0]["classification"] == "Nuclear Physics"
    assert (rows[0]["thumbnail_width"], rows[0]["thumbnail_height"]) == ("192", "267")
    assert (rows[1]["thumbnail_width"], rows[1]["thumbnail_height"]) == ("200", "150")
    assert rows[2]["thumbnail_width"] == ""
    assert rows[2]["thumbnail_height"] == ""
    assert rows[3]["date"] == "1826-12-05"
    assert rows[3]["maker"] == "Liverpool & Manchester Railway Co"
    assert rows[3]["catalogue_identifiers"] == "CHTMSS; 2017-7050"
    assert rows[3]["measurements"] == "1 item"
    assert rows[3]["archival_level"] == "fonds"
    details = json.loads(rows[0]["metadata_details_json"])
    assert details["identifier"][1]["type"] == "file number"
    assert details["creation"]["date"][0]["from"] == "1932"

    previous = (output / "image_manifest.csv").read_bytes()
    first.write_text('[{"broken":', encoding="utf-8")
    with pytest.raises(ValueError):
        match_images.build_manifest([first], images, output)
    assert (output / "image_manifest.csv").read_bytes() == previous
    assert list(output.glob("*.tmp")) == []


def test_metadata_retains_date_ranges_notes_and_multiple_identifiers():
    record = {
        "creation": {
            "date": [
                {
                    "value": "circa 1900",
                    "from": "1890",
                    "to": "1910",
                    "note": [{"value": "Estimated"}],
                },
                {"value": "circa 1900", "source": "catalogue"},
            ],
            "maker": [{"name": [{"value": "Maker, with; punctuation"}]}],
        },
        "date": [{"value": "1911", "role": [{"value": "acquired"}]}],
        "identifier": [
            {"type": "first", "value": "A;B"},
            {"type": "second", "value": "A;B"},
        ],
        "measurements": {
            "display": "1 item",
            "dimensions": [
                {"value": "1 item"},
                {"dimension": "depth", "value": 0, "units": "mm"},
            ],
        },
    }
    metadata = match_images.record_metadata(record)
    assert metadata["date"] == "circa 1900; 1911"
    assert metadata["maker"] == "Maker, with; punctuation"
    assert metadata["measurements"] == "1 item; depth: 0 mm"
    assert json.loads(metadata["metadata_details_json"]) == record
    assert all(value == "" for value in match_images.record_metadata({}).values())


@pytest.mark.parametrize(("strict", "expected"), [(False, 0), (True, 1)])
def test_strict_mode_exit_code(tmp_path, monkeypatch, strict, expected):
    summary = {
        "manifest": "manifest.csv",
        "unique_matched_images": 1,
        "unique_referenced_images": 2,
        "unique_missing_images": 1,
        "totals": {
            "missing": 1,
            "ambiguous": 0,
            "no_thumbnail": 0,
            "invalid_location": 0,
        },
    }
    path = tmp_path / "data.json"
    path.write_text("[]", encoding="utf-8")
    args = ["match_images.py", "--metadata", str(path), "--images-dir", str(tmp_path)]
    if strict:
        args.append("--fail-on-missing")
    monkeypatch.setattr(match_images, "build_manifest", lambda *args: summary)
    monkeypatch.setattr(sys, "argv", args)
    assert match_images.main() == expected
