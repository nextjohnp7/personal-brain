# PR Details — F-03: Dashboard Auth Fail-closed

## Commit
`d4d6d8e` — `fix(security): F-03 Dashboard auth fail-closed - kredensial wajib`

## Ringkasan Perubahan
- `brain_web_dashboard.py`:
  * AUTH_ENABLED selalu True; raise ValueError jika BRAIN_DASHBOARD_USER/
    BRAIN_DASHBOARD_PASSWORD_HASH tidak dikonfigurasi
  * require_login selalu enforce session (tanpa kondisi AUTH_ENABLED)
  * Hapus cek 'if not AUTH_ENABLED' di login route
- Tests: semua fixture dan test routes diupdate untuk login sebelum mengakses
  pages yang dilindungi (conftest.py, test_routes.py, test_security.py)

## Files Diubah
| File | Perubahan |
|------|-----------|
| `brain_web_dashboard.py` | Fail-closed dashboard auth: always-on, no auth-off mode |
| `tests/conftest.py` | Dashboard fixture sets test credentials via env vars |
| `tests/test_routes.py` | Login helper added; all page tests login first |
| `tests/test_security.py` | CSRF test uses logged_in_client fixture |

## Test Result
73/73 test lulus (lokal)

## Status
Siap untuk review dan merge ke `main`.