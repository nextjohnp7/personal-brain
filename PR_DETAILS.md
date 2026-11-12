# PR Details — Prioritas 2: Migrasi plaintext cleanup (Critical)

## Commit
`[pending]` — `fix(prioritas2): Migrasi plaintext cleanup`

## Ringkasan Perubahan
- `src/brain_db.py`:
  * `DB_PATH` diubah dari `"brain.db"` ke `"brain.db.enc"` — aplikasi
    sekarang membuka database terenkripsi dengan ekstensi `.enc`
  * `ENCRYPTED_PATH` tetap menunjuk ke `"brain.db.enc"` (tidak ada
    duplikasi)
- File fisik di disk:
  * `brain.db` di-rename menjadi `brain.db.enc` (197 KB, SQLCipher
    encrypted)
  * Tidak ada plaintext `brain.db` yang tersisa di disk
- `.gitignore`: sudah mencakup `brain.db`, `brain.db.enc`,
  `brain.db-wal`, `brain.db-shm`, `brain.db.enc.bak` (tidak perlu
  perubahan)

## Files Diubah
| File | Perubahan |
|------|-----------|
| `src/brain_db.py` | DB_PATH → brain.db.enc |

## Test Result
82/82 test lulus (lokal)

## Verifikasi
- `brain.db` plaintext: TIDAK ADA di disk ✓
- `brain.db.enc`: TERSEDIA di disk ✓
- `.gitignore`: mencakup kedua file ✓

## Status
Prioritas 2 — Migrasi plaintext cleanup SELESAI. Siap untuk review dan merge ke `main`.