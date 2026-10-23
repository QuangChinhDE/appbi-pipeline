"""Base Dataset — the datasets a workspace keeps, their fields, and their records.

An extapis application; `_extapi.py` compiles it. This file only declares it.

Measured on 2026-10-19 against `extapis.base.vn/dataset/v1` (workspace "Base
Ha Noi", 7 datasets):

* `datasets/get` returns every dataset in one response, each with its field
  definitions in `form[]` and `since` / `last_update` (epoch seconds)
* `dataset/get` takes `{"id"}` and returns the same object for one dataset
* `dataset/records` takes `{"dataset_id"}` and returns **at most 50 records**,
  with the true total in `num_items` / `summary.total_count`: 50 of 89 and
  50 of 5,505 on the two larger datasets. No paging parameter was found --
  page, page_id, offset, limit, per_page, cursor and a dozen others were sent
  in the JSON body, as form fields and in the query string, and none changed
  the result. Neither did any time filter (updated_from, last_update_from,
  updated_at_from, …), tested with a mark past every record's `last_update`.

Why everything is full refresh
------------------------------

Records and datasets carry real timestamps (`since`, `last_update`), so they
qualify for incremental once the API can filter or page on them. It cannot
yet, and the product owner asked for full refresh until it does. When Base
documents the paging and filter parameters, `record` gets both: paging so it
stops at 50 no longer, and incremental with the cursor picked by the
workspace from `since` / `last_update`.

Custom field values arrive under per-field keys `form_<field id>`, each an
object `{id, code, name, type, value}`. The schema stays open for them.
"""

from __future__ import annotations

from ._extapi import ExtapiConnector, ExtapiStream, Parent, nullable, obj

_SINCE = nullable("integer", en="When it was created, Unix seconds.",
                  description_vi="Thời điểm tạo, Unix timestamp (giây).")
_LAST_UPDATE = nullable("integer", en="When it was last changed, Unix seconds.",
                        description_vi="Thời điểm cập nhật gần nhất, Unix timestamp (giây).")
_USERS = {"type": "integer"}

#: A field definition, as listed on a dataset.
_FIELD = {
    "id": nullable("string", en="Field id; record values sit under form_<id>.",
                   description_vi="Id trường; giá trị trên record nằm ở khóa form_<id>."),
    "code": nullable("string", en="Field code.", description_vi="Mã trường."),
    "name": nullable("string", en="Field label.", description_vi="Tên hiển thị của trường."),
    "type": nullable("string", en="Field type (text, int, select, file…).",
                     description_vi="Kiểu trường (text, int, select, file…)."),
}

DATASET = ExtapiConnector(
    app="dataset",
    title="Dataset",
    summary=(
        "Datasets, their fields and records from Base Dataset. Records are "
        "capped at 50 per dataset until the API documents paging."
    ),
    summary_vi=(
        "Dataset, field và record từ Base Dataset. Record đang bị giới hạn 50 "
        "bản ghi mỗi dataset cho tới khi API có phân trang."
    ),
    docs_url="https://dataset.base.vn/",
    key_help=(
        "The Dataset API key (X-API-TOKEN), issued in the platform Settings "
        "app for the Dataset application. A BPM key is refused."
    ),
    key_help_vi=(
        "API key của ứng dụng Dataset (X-API-TOKEN), tạo trong ứng dụng "
        "Settings. Key của BPM sẽ bị từ chối."
    ),
    scopes="read access to datasets",
    streams=(
        ExtapiStream(
            name="dataset",
            path="datasets/get",
            extract=("dataset",),
            primary_key=("id",),
            properties={
                "id": nullable("string", en="Dataset id.", description_vi="Id dataset."),
                "name": nullable("string", en="Dataset name.", description_vi="Tên dataset."),
                "code": nullable("string", en="Dataset code.", description_vi="Mã dataset."),
                "status": nullable("integer", en="1 active.", description_vi="1 đang hoạt động."),
                "private": nullable("integer", en="1 when the dataset is private.",
                                    description_vi="1 nếu dataset ở chế độ riêng tư."),
                "user_id": nullable("integer", en="User id of the creator.", description_vi="User id người tạo."),
                "owners": nullable("array", items=_USERS, en="User ids of the owners.",
                                   description_vi="User id của người sở hữu."),
                "followers": nullable("array", items=_USERS, en="User ids of the followers.",
                                      description_vi="User id của người theo dõi."),
                "folder_id": nullable("string", en="Folder holding the dataset.",
                                      description_vi="Thư mục chứa dataset."),
                "link": nullable("string", en="Path of the dataset in Base.",
                                 description_vi="Đường dẫn dataset trên Base."),
                "form": nullable("array", items=obj(_FIELD), en="Field definitions of the dataset.",
                                 description_vi="Định nghĩa các trường của dataset."),
                "since": _SINCE,
                "last_update": _LAST_UPDATE,
                "system_id": nullable("integer", en="Base workspace id.", description_vi="Id workspace Base."),
            },
        ),
        # One row per field definition. `dataset/get` repeats what `form[]` on
        # `datasets/get` already holds, but as its own call per dataset it can
        # be extracted field by field and stamped with its dataset.
        ExtapiStream(
            name="dataset_field",
            path="dataset/get",
            extract=("dataset", "form"),
            primary_key=("dataset_id", "id"),
            body={"id": "{{ stream_partition.dataset_id }}"},
            parent=Parent(stream="dataset", partition_field="dataset_id", stamp=True),
            properties={
                "dataset_id": nullable("string", en="Dataset the field belongs to.",
                                       description_vi="Dataset chứa trường."),
                **_FIELD,
            },
        ),
        # Capped at 50 per dataset by the API today; see the module docstring.
        ExtapiStream(
            name="record",
            path="dataset/records",
            extract=("records",),
            primary_key=("id",),
            body={"dataset_id": "{{ stream_partition.dataset_id }}"},
            parent=Parent(stream="dataset", partition_field="dataset_id"),
            properties={
                "id": nullable("string", en="Record id.", description_vi="Id record."),
                "dataset_id": nullable("string", en="Dataset the record belongs to.",
                                       description_vi="Dataset chứa record."),
                "name": nullable("string", en="Record name.", description_vi="Tên record."),
                "user_id": nullable("integer", en="User id of the creator.", description_vi="User id người tạo."),
                "owners": nullable("array", items=_USERS, en="User ids of the owners.",
                                   description_vi="User id của người sở hữu."),
                "followers": nullable("array", items=_USERS, en="User ids of the followers.",
                                      description_vi="User id của người theo dõi."),
                "collaborators": nullable("array", items=_USERS, en="User ids of collaborators.",
                                          description_vi="User id của người cộng tác."),
                "link": nullable("string", en="Path of the record in Base.",
                                 description_vi="Đường dẫn record trên Base."),
                "form": nullable("array", items={"type": "string"},
                                 en="Ids of the fields the record carries; each value is under form_<id>.",
                                 description_vi="Id các trường của record; giá trị nằm ở khóa form_<id>."),
                "since": _SINCE,
                "last_update": _LAST_UPDATE,
                "system_id": nullable("integer", en="Base workspace id.", description_vi="Id workspace Base."),
            },
        ),
    ),
)
