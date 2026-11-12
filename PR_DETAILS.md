# PR Details — Prioritas 1: Gmail failed-message checkpoint (Critical)

## Commit
`[pending]` — `fix(prioritas1): Gmail failed-message checkpoint`

## Ringkasan Perubahan
- `src/brain_db.py`:
  * Tabel baru `gmail_failed_messages` (message_id PK, error_message,
    failed_at, retry_count, last_retry_at)
  * `record_failed_gmail_message(conn, message_id, error_message)` —
    catat pesan gagal, increment retry_count pada duplikasi
  * `get_failed_gmail_messages(conn)` — ambil semua pesan menunggu retry,
    urutkan by failed_at ASC
  * `clear_failed_gmail_message(conn, message_id)` — hapus setelah retry
    berhasil
- `brain_sync_daemon.py`:
  * Sebelum sinkronisasi normal, coba ulang pesan gagal dari run sebelumnya
  * Saat `_process_gmail_message` gagal (history loop dan timestamp
    fallback), catat ke `gmail_failed_messages` bukan cuma di-log
  * Saat retry berhasil, hapus dari daftar gagal
- `tests/test_security.py`:
  * `test_failed_gmail_message_roundtrip` — catat, ambil, update
    retry_count, hapus
  * `test_failed_gmail_message_ordering` — urutkan by failed_at
  * `test_clear_nonexistent_failed_gmail_message` — tidak error

## Files Diubah
| File | Perubahan |
|------|-----------|
| `src/brain_db.py` | Tabel gmail_failed_messages + 3 fungsi |
| `brain_sync_daemon.py` | Retry pesan gagal + catat kegagalan |
| `tests/test_security.py` | 3 tes failed-message checkpoint |

## Test Result
82/82 test lulus (lokal)

## Status
Prioritas 1 — Gmail failed-message checkpoint SELESAI. Siap untuk review dan merge ke `main`.