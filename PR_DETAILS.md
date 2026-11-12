# PR Details — F-02: MCP Fail-closed Auth

## Commit
`622008c` — `fix(security): F-02 MCP fail-closed auth - API key wajib`

## Ringkasan Perubahan
- `_check_mcp_api_key()` di `brain_mcp_server.py` sekarang deny-all jika tidak ada kunci MCP yang dikonfigurasi (fail-closed)
- Missing key saat auth aktif → ditolak dengan pesan "Missing MCP API key"
- Tanpa `BRAIN_MCP_API_KEY_READ`/`BRAIN_MCP_API_KEY_WRITE`, semua request MCP ditolak
- Test `test_no_auth_configured_allows_all` diubah menjadi `test_no_auth_configured_denies_all`
- Test baru `test_missing_key_when_auth_configured` ditambahkan

## Files Diubah
| File | Perubahan |
|------|-----------|
| `brain_mcp_server.py` | Fail-closed MCP auth: deny-all when no keys configured |
| `tests/test_mcp_auth.py` | Updated + new test for missing key |

## Test Result
73/73 test lulus (lokal)

## Status
Siap untuk review dan merge ke `main`.