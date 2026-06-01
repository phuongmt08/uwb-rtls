"""
===============================================================================
  UWB RTLS Studio — Session Browser
===============================================================================
  File        : repository/session_browser.py
  Description : Logic cho việc browse, filter, và review past sessions.
                Cho phép User/Developer mở session cũ để debug/phân tích.

  MVVM Role   : REPOSITORY — read-only query layer.

  Chức năng:
    - List tất cả sessions đã lưu (sorted by date, newest first)
    - Filter sessions theo:
        • Date range (từ ngày → đến ngày)
        • Session type (RANGING / STREAMING / LOG)
        • Device type (TAG / ANCHOR)
        • Serial number
        • Duration range
    - Load session data (positions, logs, config)
    - Preview session summary (quick view without full load)
    - Delete old sessions (manual cleanup)
    - Search trong session logs

  Được sử dụng bởi:
    - SessionBrowserViewModel  → list + filter + load
    - SessionBrowserView       → hiển thị history table

  NOTE: Session Browser có thể được tích hợp vào Log Tab hoặc
        tạo thành 1 dialog/popup riêng tùy design decision.
        Hiện tại nó sẽ là 1 section trong Log Tab.
===============================================================================
"""
pass
