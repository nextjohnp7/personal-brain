# PR Details — F-04: Export/Import Require BRAIN_API_KEY

## Commit
`c3999a6` — `fix(security): F-04 Export/Import require BRAIN_API_KEY - fail-closed`

## Ringkasan Perubahan
- `brain_web_dashboard.py`:
  * `BRAIN_API_KEY` wajib di-set; `raise ValueError` jika kosong (fail-closed)
  * `require_api_key()` selalu enforce (tidak ada kondisi `if BRAIN_API_KEY`)
  * `/export` dan `/import` selalu dilindungi API key
- Tests: `conftest.py` set `BRAIN_API_KEY` env; `test_routes.py` sertakan
  `X-API-Key` header untuk `/export` dan `/import`

## Files Diubah
| File | Perubahan |
|------|-----------|
| `brain_web_dashboard.py` | Fail-closed API key: always-on, no unset mode |
| `tests/conftest.py` | Dashboard fixture sets BRAIN_API_KEY via env var |
| `tests/test_routes.py` | API key header for /export and /import tests |

## Test Result
73/73 test lulus (lokal)

## Status
Siap untuk review dan merge ke `main`.