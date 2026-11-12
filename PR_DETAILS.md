# PR Details — Prioritas 3: Stable embedding hashing (High)

## Commit
`[pending]` — `fix(prioritas3): Stable embedding hashing`

## Ringkasan Perubahan
- `src/brain_embeddings.py`: Sudah menggunakan `hashlib.md5` via
  `_stable_hash()` (tidak ada `hash()` built-in di kode) —
  verifikasi konfirmasi tidak ada penggunaan `hash()` di src/
- `tests/test_security.py`:
  * `test_stable_hash_deterministic` — _stable_hash menghasilkan nilai
    yang sama untuk input yang sama, dalam range [0, dim)
  * `test_tfidf_embed_deterministic` — _tfidf_embed menghasilkan vektor
    yang sama untuk teks yang sama, vektor nol untuk teks kosong

## Files Diubah
| File | Perubahan |
|------|-----------|
| `tests/test_security.py` | 2 tes stable embedding hashing |

## Test Result
84/84 test lulus (lokal)

## Verifikasi
- Tidak ada `hash()` built-in di `src/` ✓
- `_stable_hash` menggunakan `hashlib.md5(token).digest()[:4]` ✓
- Vektor deterministik across repeated calls ✓

## Status
Prioritas 3 — Stable embedding hashing SELESAI. Siap untuk review dan merge ke `main`.