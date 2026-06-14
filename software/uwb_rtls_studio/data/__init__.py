"""
Data layer for UWB RTLS Studio.

This package only keeps raw-oriented data primitives and in-memory stores.
Session persistence is file-based under `data/sessions/`, while parsing,
mapping, and encode/decode responsibilities live in repository/service layers.

Current session storage layout:

    data/
      sessions/
        index.json
        SES_YYYYMMDD_HHMMSS_<type>/
          session_meta.json
          config_snapshot.json
          anchors.json
          positions.csv
          logs.csv
          logs.txt

Legacy SQL/SQLite artifacts are no longer part of the runtime architecture.
"""
