"""
Project-shared code: the XLSX export mechanisms and the export plumbing.

`xlsx_stream` owns the streaming raw-XML XLSX mechanics; `export` owns the
request validation and rendering of the dictionary exports — things that must
not live in a concrete project («Сырьё», «Ремонт», КППК) when every project
needs the same behavior.
"""
