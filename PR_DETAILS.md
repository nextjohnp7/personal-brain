# PR Details — F-01: Fail-closed Enkripsi Database

## Commit
`2a6afbd` — `fix(security): F-01 Fail-closed enkripsi database - BRAIN_DB_KEY wajib`

## Ringkasan Perubahan
- `_get_key_hex()` di `src/brain_db.py` sekarang raise `ValueError` jika `BRAIN_DB_KEY` tidak di-set atau kosong (fail-closed, tidak ada fallback plaintext)
- `get_connection()` selalu terapkan `PRAGMA key` (SQLCipher AES-256 per-page encryption)
- Database selalu terenkripsi at-rest; tidak ada file plaintext temp yang pernah ditulis
- Semua test fixture diupdate untuk menggunakan test `BRAIN_DB_KEY` (tidak ada mode plaintext di test)

## Files Diubah
| File | Perubahan |
|------|-----------|
| `src/brain_db.py` | Fail-closed key validation, always-on PRAGMA key |
| `tests/conftest.py` | Set test key instead of delenv |
| `tests/test_connectors_mock.py` | Set test key instead of empty string (4x) |
| `tests/test_db_unit.py` | db_mode fixture always sets key |
| `tests/test_db_concurrency.py` | db_mode fixture always sets key |
| `tests/test_export_import_roundtrip.py` | db_mode fixture always sets key |

## Test Result
72/72 test lulus (lokal)

## Status
Siap untuk review dan merge ke `main`.