# PR Details — Prioritas 4: Performance & Optimasi

## Commit
`eb6da4c` — `feat(prioritas4): Performance & Optimasi`

## Ringkasan Perubahan
- `src/brain_db.py`:
  * 20 CREATE INDEX pada kolom sering diquery (contacts email/name, emails
    sender/recipient/received_at, notes title/updated_at,
    knowledge_entries title/created_at, entities name/type,
    entity_relations source/target/type, item_shares item/user,
    embeddings source_type, conversations created_at,
    provenance_entries subject)
  * `semantic_search()`: batasi prefetch ke max(limit*20, 500) baris sebelum
    hitung cosine similarity di Python (bounded O(n) sebagai gantian O(n)
    penuh pada tabel embeddings yang besar)
  * `backup_brain_db()`: tambah `PRAGMA wal_checkpoint(TRUNCATE)` sebelum
    `VACUUM INTO` agar backup konsisten
- `src/brain_embeddings.py`:
  * Tambah `_embed_cache` (dict, capped `_EMBED_CACHE_MAX=1000`) pada
    `embed_text()` — teks sama tidak di-embed ulang dalam satu proses
- `tests/test_security.py`:
  * `test_indexes_exist`, `test_search_pagination_limits_results`,
    `test_embed_text_cache_returns_same_vector`
  * `db_mode` fixture untuk tes performa

## Files Diubah
| File | Perubahan |
|------|-----------|
| `src/brain_db.py` | Indeks, semantic_search prefetch limit, backup WAL checkpoint |
| `src/brain_embeddings.py` | Cache embed_text |
| `tests/test_security.py` | 4 tes performa + db_mode fixture |

## Test Result
79/79 test lulus (lokal)

## Status
Prioritas 4 — Performance & Optimasi SELESAI. Siap untuk review dan merge ke `main`.