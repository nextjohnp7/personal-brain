"""Manajer Brain Lokal (Opsi A) - tanpa Google/Twitter.

Fitur:
- Tambah/update kontak secara manual
- Sinkronisasi catatan markdown dari folder lokal
- Cari di brain (kontak, notes, knowledge)
- Lihat semua kontak dan notes
"""

import sys
import os
from pathlib import Path

# Pastikan src bisa diimpor
sys.path.insert(0, str(Path(__file__).parent / "src"))

from brain_db import (
    get_connection, upsert_contact, search_contacts, get_contact,
    upsert_note, search_notes, global_search, insert_knowledge_entry,
    log_conversation,
)
from brain_connectors import sync_markdown_notes


def tambah_kontak(conn):
    print("\n=== TAMBAH KONTAK ===")
    nama = input("Nama: ").strip()
    email = input("Email (opsional): ").strip() or None
    telepon = input("Telepon (opsional): ").strip() or None
    perusahaan = input("Perusahaan (opsional): ").strip() or None
    jabatan = input("Jabatan (opsional): ").strip() or None
    catatan = input("Catatan (opsional): ").strip() or None

    cid = upsert_contact(
        conn, name=nama, email=email, phone=telepon,
        company=perusahaan, role=jabatan,
        source='manual', context_notes=catatan
    )
    print(f"Kontak disimpan dengan ID: {cid}")


def lihat_kontak(conn):
    print("\n=== DAFTAR KONTAK ===")
    kontak = search_contacts(conn, "", limit=100)
    if not kontak:
        print("(belum ada kontak)")
        return
    for k in kontak:
        print(f"#{k['id']} | {k['name']} | {k.get('email', '-')} | {k.get('company', '-')}")


def cari_kontak(conn):
    print("\n=== CARI KONTAK ===")
    q = input("Kata kunci: ").strip()
    hasil = search_contacts(conn, q, limit=20)
    if not hasil:
        print("(tidak ditemukan)")
        return
    for k in hasil:
        print(f"#{k['id']} | {k['name']} | {k.get('email', '-')} | {k.get('company', '-')}")
        if k.get('context_notes'):
            print(f"    Catatan: {k['context_notes']}")


def sinkron_notes(conn):
    print("\n=== SINKRONISASI NOTES ===")
    folder = os.environ.get('BRAIN_NOTES_FOLDER', 'D:/Project GitHub Brain Memory/Notes')
    try:
        stats = sync_markdown_notes(folder)
        print(f"Selesai. Notes disinkronkan: {stats['notes']}")
    except Exception as e:
        print(f"Error: {e}")


def cari_notes(conn):
    print("\n=== CARI NOTES ===")
    q = input("Kata kunci: ").strip()
    hasil = search_notes(conn, q, limit=20)
    if not hasil:
        print("(tidak ditemukan)")
        return
    for n in hasil:
        print(f"#{n['id']} | {n.get('title', '-')} | {n.get('filename', '-')}")
        if n.get('summary'):
            print(f"    Ringkasan: {n['summary'][:150]}")


def cari_semua(conn):
    print("\n=== CARI DI SEMUA BRAIN ===")
    q = input("Kata kunci: ").strip()
    hasil = global_search(conn, q, limit=10)
    print(f"Kontak: {len(hasil['contacts'])}")
    print(f"Email: {len(hasil['emails'])}")
    print(f"Kalender: {len(hasil['events'])}")
    print(f"Tweet: {len(hasil['tweets'])}")
    print(f"Notes: {len(hasil['notes'])}")
    print(f"Knowledge: {len(hasil['knowledge'])}")


def main():
    conn = get_connection()
    print("Brain Lokal siap. Database:", conn)

    while True:
        print("\n=== MENU BRAIN ===")
        print("1. Tambah kontak")
        print("2. Lihat semua kontak")
        print("3. Cari kontak")
        print("4. Sinkronisasi notes dari folder")
        print("5. Cari notes")
        print("6. Cari di semua brain")
        print("0. Keluar")

        pilihan = input("\nPilih: ").strip()

        if pilihan == '1':
            tambah_kontak(conn)
        elif pilihan == '2':
            lihat_kontak(conn)
        elif pilihan == '3':
            cari_kontak(conn)
        elif pilihan == '4':
            sinkron_notes(conn)
        elif pilihan == '5':
            cari_notes(conn)
        elif pilihan == '6':
            cari_semua(conn)
        elif pilihan == '0':
            print("Sampai jumpa!")
            break
        else:
            print("Pilihan tidak valid.")

    conn.close()


if __name__ == "__main__":
    main()