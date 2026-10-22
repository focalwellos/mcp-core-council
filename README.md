# mcp-core-council

Client của **MCP-core council**. Khi bạn làm việc trong Claude Code hoặc Codex, công cụ này lấy ý kiến độc lập từ nhiều model cho cùng một việc: phản biện, kiểm chứng, đóng vai đối thủ, hoặc đề xuất hướng khác.

- **Model chạy trên máy bạn, bằng gói thuê bao của chính bạn** (Claude, ChatGPT/Codex, Google/Antigravity). Không ai khác trả tiền hay giữ đăng nhập của bạn.
- **Server MCP-core không bao giờ nhận nội dung của bạn**, gồm câu hỏi, ngữ cảnh và câu trả lời. Server chỉ gửi xuống vai trò của từng ghế, rồi nhận lại các con số: số ghế, tên model, số token, độ dài câu trả lời.
- Kết quả luôn ghi `verified: false`. Đó là ý kiến tham khảo, không phải kiểm chứng độc lập.

*English: a stdio MCP server that runs review-council seats on your own CLI subscriptions. The MCP-core server only plans the roles and records metadata; it never receives your question, context or answers. Access needs a token: request one with `mcp-core-council request` (it arrives by email), then `mcp-core-council login <token>`.*

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

## 1. Xin token

Điền tên và email tại <https://35-253-26-216.sslip.io/access>, hoặc chạy:

```
COUNCIL request --name "Tên của bạn" --email ban@example.com --note "Dùng để phản biện kế hoạch dự án"
```

Token được gửi tới email của bạn sau vài giây. Hãy kiểm tra cả mục spam. Mỗi token có hạn dùng, ngày hết hạn ghi trong email. Nếu mất token hoặc token hết hạn, bạn gửi yêu cầu lại; token mới sẽ thay token cũ.

## 2. Lưu token trên máy

```
COUNCIL login <token trong email>
```

Lệnh này kiểm tra token với server, rồi lưu vào `~/.config/mcp-core/council-access.json`. Trên Windows, file nằm ở `%USERPROFILE%\.config\mcp-core\`. Đừng chia sẻ file này cho ai.

## 3. Khai báo các ghế

```
COUNCIL init
```

Lệnh này tìm `claude`, `codex` và `agy` trên máy, rồi ghi file `~/.config/mcp-core/council-local.json`, mỗi CLI tìm thấy thì có ghế tương ứng:

- `claude`: hai ghế `claude:sonnet` và `claude:haiku`.
- `codex`: một ghế với model bạn đang dùng trong Codex, tức dòng `model = ...` trong `~/.codex/config.toml`.
- `agy`: một ghế với model Gemini đầu tiên mà `agy models` liệt kê cho tài khoản Google của bạn.

Vì vậy, trước khi chạy `init`, hãy mở `codex` và `agy` một lần để đăng nhập. Nếu `init` không tìm được model, nó in ra dòng mẫu để bạn tự thêm vào `"seats"`, ví dụ:

```json
{"label": "agy:gemini-3.8-flash-high", "cli": "agy", "model": "gemini-3.8-flash-high"}
```

Nếu file đã có sẵn, `init` không ghi đè. Nó in ra những ghế tìm thấy trên máy mà file còn thiếu. Bạn chép các dòng đó vào, hoặc chạy `COUNCIL init --force` để ghi lại file từ đầu.

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

Nếu một ghế không chạy được (chưa đăng nhập, hết quota, thiếu CLI, quá giờ), vai của nó được chạy lại trên một ghế khác trong file. Ưu tiên ghế chưa dùng trong vòng, rồi tới ghế của CLI khác. Kết quả của ghế đó có `fallback_from` ghi ghế ban đầu và lý do. Nếu chỉ còn một ghế chạy được, nhiều vai sẽ cùng do ghế đó trả lời, và `shares_seat_with` cho biết điều đó. Khi ấy câu trả lời không còn độc lập. Muốn có ghế dự phòng cùng hãng, hãy thêm một ghế nữa, ví dụ một model codex thứ hai:

```json
{"label": "codex:gpt-6.1-sol", "cli": "codex", "model": "gpt-6.1-sol"}
```

## Cập nhật

`uvx` lấy bản mới nhất trên nhánh `main`. Để buộc tải lại, thêm `--refresh` sau `uvx`.

## Gặp lỗi

- `no valid access token`: chưa có token, token hết hạn hoặc đã bị thu hồi. Gửi yêu cầu lại (bước 1) rồi `COUNCIL login` với token mới.
- Không thấy email: kiểm tra mục spam. Mỗi email chỉ được xin 3 lần mỗi ngày.
- `claude not found on PATH`: CLI chưa cài, hoặc chưa có trong PATH của phiên Claude Code.
- `daily cap reached`: đã dùng hết 100 vòng trong ngày (tính theo giờ UTC).
- Trên **Windows**, bản này chưa được thử trên máy thật. Bạn gặp lỗi gì, xin gửi lại nguyên văn thông báo lỗi.

## Cấu hình nâng cao

| Biến môi trường | Ý nghĩa |
|---|---|
| `MCP_REMOTE_URL` | Server khác với server công khai mặc định |
| `MCP_AUTH_TOKEN` | Token dùng thay cho file access |
| `MCP_COUNCIL_LOCAL_CONFIG` | Đường dẫn khác cho `council-local.json` |
| `MCP_COUNCIL_ACCESS_FILE` | Đường dẫn khác cho `council-access.json` |

### Nhiều tài khoản của cùng một hãng

Phần này chỉ dành cho người có từ hai gói thuê bao Claude, hoặc hai gói ChatGPT/Codex, trở lên và muốn các ghế chạy trên những tài khoản khác nhau. Có một tài khoản cho mỗi hãng thì bạn bỏ qua phần này.

Mỗi CLI giữ phần đăng nhập trong một thư mục: `~/.claude` cho Claude, `~/.codex` cho Codex. Tài khoản thứ hai cần một thư mục khác, và bạn đăng nhập vào thư mục đó một lần.

macOS/Linux:

```
CLAUDE_CONFIG_DIR=~/.claude-work claude
CODEX_HOME=~/.codex-work codex login
```

Windows (PowerShell):

```
$env:CLAUDE_CONFIG_DIR="$HOME\.claude-work"; claude
$env:CODEX_HOME="$HOME\.codex-work"; codex login
```

Đăng nhập xong thì thoát. Sau đó thêm `config_dir` vào ghế dùng tài khoản đó. Mỗi ghế phải có `label` riêng:

```json
{"label": "claude:sonnet", "cli": "claude", "model": "sonnet"}
{"label": "claude-work:sonnet", "cli": "claude", "model": "sonnet", "config_dir": "~/.claude-work"}
```

Ghế không có `config_dir` vẫn dùng tài khoản mặc định. Đường dẫn chỉ nằm trên máy bạn, không gửi lên server. `config_dir` dùng được cho ghế `claude` và `codex`. Antigravity (`agy`) chưa hỗ trợ.

Nếu thư mục không tồn tại, ghế báo `unavailable` với thông báo `config_dir … not found`. Khi đó bạn đăng nhập lại như trên.
