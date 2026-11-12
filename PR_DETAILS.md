# PR Details — F-05 SQL Table Name Allowlist Validation

## Commit
`20e03ed` — `fix(security): F-05 SQL table name allowlist validation - defense-in-depth`

## Ringkasan Perubahan
- `src/brain_db.py`:
  * Tambah `_TABLE_NAME_RE` (`^[a-zA-Z_][a-zA-Z0-9_]*$`) dan `_validate_table_name()`
  * Terapkan ke 4 lokasi f-string SQL: `export_brain_json`, `get_shared_items`, `check_item_access`, `get_db_stats`
  * Mencegah SQL injection jika nama tabel berubah dari input user di masa depan

## Files Diubah
| File | Perubahan |
|------|-----------|
| `src/brain_db.py` | Table name allowlist validation for all f-string SQL |

## Test Result
73/73 test lulus (lokal)

## Status
Siap untuk review dan merge ke `main`.