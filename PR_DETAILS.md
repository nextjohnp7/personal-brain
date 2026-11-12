# PR Details — F-07 Assertion Tests per F-ID

## Commit
`7eefe4d` — `fix(security): F-07 Assertion tests per F-ID`

## Ringkasan Perubahan
- `tests/test_security.py`:
  * `test_brain_db_key_required`: F-01 — `get_connection()` gagal jika `BRAIN_DB_KEY`
    tidak di-set (fail-closed enkripsi)
  * `test_validate_table_name_rejects_malicious`: F-05 — `_validate_table_name()`
    tolak nama tabel berbahaya (SQL injection), terima nama valid

## Files Diubah
| File | Perubahan |
|------|-----------|
| `tests/test_security.py` | Assertion tests untuk F-01 dan F-05 |

## Test Result
76/76 test lulus (lokal)

## Status
Prioritas 3 — Security SELESAI (F-01 sampai F-07). Semua 76 test lulus.
Siap untuk review dan merge ke `main`.