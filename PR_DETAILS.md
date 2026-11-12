# PR Details — Prioritas 4: Attention-mask pooling (High)

## Commit
`[pending]` — `fix(prioritas4): Attention-mask-aware pooling`

## Ringkasan Perubahan
- `src/brain_embeddings.py`: Sudah menggunakan `_mean_pool_with_attention_mask()`
  yang weight token embeddings by attention_mask sebelum averaging —
  tidak ada bare `outputs.last_hidden_state.mean(dim=1)` (verifikasi
  konfirmasi kode sudah benar)
- `tests/test_security.py`:
  * `test_attention_mask_pooling_ignores_padding` — verifikasi pooling
    mengabaikan padding token (skip jika torch tidak tersedia)

## Files Diubah
| File | Perubahan |
|------|-----------|
| `tests/test_security.py` | 1 tes attention-mask pooling (skip tanpa torch) |

## Test Result
84 passed, 1 skipped (lokal)

## Verifikasi
- `_mean_pool_with_attention_mask` multiply token embeddings by mask ✓
- `sum_mask = torch.clamp(input_mask_expanded.sum(1), min=1e-9)` mencegah div-by-zero ✓
- Tidak ada bare `last_hidden_state.mean(dim=1)` ✓

## Status
Prioritas 4 — Attention-mask pooling SELESAI. Siap untuk review dan merge ke `main`.