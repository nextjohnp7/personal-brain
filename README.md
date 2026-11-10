# Personal Brain - Knowledge Base untuk AI Agent

## Struktur
```
D:/Project GitHub Brain Memory/
├── src/
│   ├── brain_db.py              # SQLite database schema & operasi
│   ├── brain_connectors.py      # Konektor Gmail, Calendar, Twitter, Notes
│   └── brain_integration.py     # Alur baca-tulis brain (pre-post response)
├── brain.db                     # Database SQLite (dibuat otomatis saat pertama kali dijalankan)
├── Notes/                       # Folder catatan markdown (sesuaikan BRAIN_NOTES_FOLDER di .env)
├── .env.example                 # Salin ke .env dan isi API keys
└── README.md
```

## Setup

1. Salin `.env.example` menjadi `.env` dan isi API keys:
   - `GOOGLE_CLIENT_ID` dan `GOOGLE_CLIENT_SECRET` — dari Google Cloud Console
   - `TWITTER_BEARER_TOKEN` — dari developer.twitter.com
   - `BRAIN_NOTES_FOLDER` — path ke folder catatan markdown Anda

2. Install dependensi Python:
   ```bash
   pip install google-auth google-auth-oauthlib google-auth-httplib2 google-api-python-client requests
   ```

3. Jalankan sinkronisasi pertama:
   ```bash
   cd src
   python -c "
   from brain_connectors import sync_all
   # Contoh: sinkronisasi notes saja
   result = sync_all(notes_folder='D:/Project GitHub Brain Memory/Notes')
   print(result)
   "
   ```

## Alur Kerja Brain

- **Sebelum agen menjawab**: `get_brain_context(query)` mencari kontak, email, kalender, notes, dan knowledge yang relevan, lalu disisipkan ke prompt agen.
- **Setelah agen menjawab**: percakapan dan entitas baru (nama, email, topik) disimpan kembali ke `brain.db` untuk pembelajaran seiring waktu.

## Tabel Database (brain.db)

| Tabel | Isi |
|-------|-----|
| `contacts` | Kontak orang (nama, email, telepon, perusahaan, catatan) |
| `emails` | Email dari Gmail |
| `calendar_events` | Event dari Google Calendar |
| `tweets` | Tweet dari Twitter/X |
| `notes` | Catatan markdown dari folder lokal |
| `knowledge_entries` | Pengetahuan baru (koneksi antar orang, ide, temuan) |
| `conversations` | Riwayat percakapan agen |

Semua data tersimpan **lokal** di komputer Anda. Tidak ada server yang diperlukan.