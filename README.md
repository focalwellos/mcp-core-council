# mcp-core-council

Client của **MCP-core council**. Khi bạn làm việc trong Claude Code hoặc Codex, công cụ này lấy ý kiến độc lập từ nhiều model cho cùng một việc: phản biện, kiểm chứng, đóng vai đối thủ, hoặc đề xuất hướng khác.

- **Model chạy trên máy bạn, bằng gói thuê bao của chính bạn** (Claude, ChatGPT/Codex, Google/Antigravity). Không ai khác trả tiền hay giữ đăng nhập của bạn.
- **Server MCP-core không bao giờ nhận nội dung của bạn**, gồm câu hỏi, ngữ cảnh và câu trả lời. Server chỉ gửi xuống vai trò của từng ghế, rồi nhận lại các con số: số ghế, tên model, số token, độ dài câu trả lời.
- Kết quả luôn ghi `verified: false`. Đó là ý kiến tham khảo, không phải kiểm chứng độc lập.

*English: a stdio MCP server that runs review-council seats on your own CLI subscriptions. The MCP-core server only plans the roles and records metadata; it never receives your question, context or answers. Access needs a token: request one with `mcp-core-council request`.*

## Cần có

- Claude Code (hoặc Codex), **đăng nhập bằng gói thuê bao** của bạn.
- **git**. Trên Windows, cài [Git for Windows](https://git-scm.com/download/win).
- **uv**, công cụ cài Python tự động:
  - Windows (PowerShell): `powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"`
  - macOS/Linux: `curl -LsSf https://astral.sh/uv/install.sh | sh`

Trong các lệnh dưới đây, `COUNCIL` là viết tắt của:

```
uvx --from git+https://github.com/focalwellos/mcp-core-council mcp-core-council
```

## 1. Xin quyền dùng

```
COUNCIL request --name "Tên của bạn" --email ban@example.com --note "Dùng để phản biện kế hoạch dự án"
```

Yêu cầu được người quản lý duyệt bằng tay. Mã yêu cầu được lưu trong `~/.config/mcp-core/council-access.json`. Trên Windows, file nằm ở `%USERPROFILE%\.config\mcp-core\`. Đừng chia sẻ file này cho ai.

Bạn cũng có thể xin quyền trên trình duyệt tại <https://35-253-26-216.sslip.io/access>.

## 2. Nhận token

```
COUNCIL status
```

Khi được duyệt, lệnh này nhận token và lưu vào file trên, bạn không cần chép tay. Token chỉ được trao **một lần**. Nếu mất token, hãy xin lại. Mỗi token có hạn dùng; hết hạn thì gửi yêu cầu mới bằng `request --force`.

## 3. Khai báo các ghế

```
COUNCIL init
```

Lệnh này tìm `claude`, `codex` và `agy` trên máy, rồi ghi file `~/.config/mcp-core/council-local.json`. Nếu có `claude`, lệnh tạo sẵn hai ghế `claude:sonnet` và `claude:haiku`. Model của Codex và Antigravity tùy tài khoản, nên bạn tự thêm vào `"seats"`, mỗi model một dòng:

```json
{"label": "codex:gpt-6.1-sol", "cli": "codex", "model": "gpt-6.1-sol"}
{"label": "agy:gemini-3.8-flash-high", "cli": "agy", "model": "gemini-3.8-flash-high"}
```

Lệnh `agy models` liệt kê các model mà tài khoản Google của bạn dùng được.

## 4. Đăng ký với Claude Code (một lần, dùng cho mọi project)

```
claude mcp add council-local -s user -- uvx --from git+https://github.com/focalwellos/mcp-core-council mcp-core-council
```

Mở một phiên Claude Code mới, rồi kiểm bằng `claude mcp get council-local`. Kết quả phải là `✔ Connected`.

## 5. Dùng

Nói với Claude Code, ví dụ:

- "Dùng council_run loại **critique** để phản biện kế hoạch này."
- "Chạy council **red-team** cho đề xuất giá này."
- Các loại hiện có: `critique`, `verify`, `red-team`, `alternatives`.

Mỗi ghế là một lần gọi model bằng gói thuê bao của bạn, nên sẽ tính vào quota của bạn. Mỗi token được tối đa 100 vòng mỗi ngày.

## Cập nhật

`uvx` lấy bản mới nhất trên nhánh `main`. Để buộc tải lại, thêm `--refresh` sau `uvx`.

## Gặp lỗi

- `no valid access token`: chưa có token, token hết hạn hoặc đã bị thu hồi. Chạy `COUNCIL status`, hoặc `COUNCIL request --force`.
- `claude not found on PATH`: CLI chưa cài, hoặc chưa có trong PATH của phiên Claude Code.
- `daily cap reached`: đã dùng hết 100 vòng trong ngày (tính theo giờ UTC).
- Trên **Windows**, bản này chưa được thử trên máy thật. Bạn gặp lỗi gì, xin gửi lại nguyên văn thông báo lỗi.

## Cấu hình nâng cao

| Biến môi trường | Ý nghĩa |
|---|---|
| `MCP_REMOTE_URL` | Server khác với server công khai mặc định |
| `MCP_AUTH_TOKEN` | Token do người quản lý gửi trực tiếp (dùng thay cho file access) |
| `MCP_COUNCIL_LOCAL_CONFIG` | Đường dẫn khác cho `council-local.json` |
| `MCP_COUNCIL_ACCESS_FILE` | Đường dẫn khác cho `council-access.json` |
