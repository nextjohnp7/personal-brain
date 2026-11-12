# PR Details — Prioritas 5: CLI Tooling (Opsi A)

## Commit
`[pending]` — `feat(prioritas5): CLI tooling - rotate key + user management`

## Ringkasan Perubahan
- `scripts/rotate_key.py` (baru): CLI rotate SQLCipher encryption key
  * `--old-key` / env `BRAIN_DB_KEY` for current key
  * `--new-key` or auto-generate new base64 32-byte key
  * Wraps `brain_db.rotate_db_key()`
- `manage_brain.py`: Tambah manajemen user (menu 7 & 8)
  * `create_user_cmd()`: buat user baru (id, username, email, password, role)
  * `list_users_cmd()`: daftar semua user dengan role dan created_at
  * Import `create_user`, `get_user_by_username` dari `brain_db`

## Files Diubah
| File | Perubahan |
|------|-----------|
| `scripts/rotate_key.py` | Baru — CLI rotate encryption key |
| `manage_brain.py` | Tambah create user + list user menu |

## Test Result
79/79 test lulus (lokal)
Rotate key verifikasi manual: PASSED (contact tersimpan di DB baru dengan kunci baru)
manage_brain.py import: OK

## Status
Prioritas 5 — CLI Tooling (Opsi A) SELESAI. Siap untuk review dan merge ke `main`.