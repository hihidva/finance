# Module 4: Knowledge Base (Open-loop user-fed RAG)

## 4.1 Mục đích

Cho phép user inject kiến thức/kinh nghiệm thủ công vào RAG để LLM arbiter tham khảo — bổ sung cho closed-loop học từ `outcomes` (Module 3).

**Use case điển hình:**
- "DXY > 105 thường ép vàng giảm trong 1-2 tuần kế."
- "MBB hay tăng mạnh sau ngày GDKHQ trả cổ tức tiền mặt."
- "BTC bị bán mạnh khi Fed phát biểu hawkish 30 ngày trước họp FOMC."

**Đầu vào:** title + body + tags do user nhập (hoặc đọc từ file).

**Đầu ra:**
- Row trong `knowledge` table (canonical source, MySQL)
- Embedding trong ChromaDB `knowledge` collection
- Liên kết bằng `chroma_id` (UUID)

## 4.2 Entities

### `knowledge`

| Trường | Kiểu | Mô tả |
|---|---|---|
| `id` | int PK | |
| `title` | varchar(256) | Tiêu đề ngắn |
| `body` | text | Nội dung đầy đủ (tiếng Việt OK, tiếng Anh OK) |
| `tags` | JSON list | `["xau", "dxy", "macro"]` |
| `source` | varchar(32) | `user`, `import`, ... (default `user`) |
| `chroma_id` | varchar(64) nullable | UUID liên kết với ChromaDB |
| `is_active` | boolean | False = soft-delete (giữ history, gỡ khỏi RAG) |
| `created_at`, `updated_at` | datetime | |

## 4.3 Quy trình

### Add knowledge

```
add-knowledge --title "..." --body "..." --tags xau,dxy,macro
```

[main.py cmd_add_knowledge](../../main.py):

```
1. Read body từ --body, --body-file, hoặc stdin
2. session 1: insert_knowledge → kb_id
3. learn_knowledge(kb_id, title, body, tags) → embedding lên Chroma → chroma_id (UUID)
4. session 2: update_knowledge_chroma_id(kb_id, chroma_id)
5. Print "OK: knowledge id=N, chroma_id=..."
```

Body rỗng → exit code 2, không insert.

### List knowledge

```
list-knowledge
```

In ra mọi entry (cả active + inactive) với marker `[ ]`/`[X]`, ID, tags, title, body preview 60 ký tự.

### Sync knowledge (re-embed all)

[jobs/sync_knowledge.py](../../src/finance_bot/jobs/sync_knowledge.py):

```
For each kb in knowledge where is_active=True:
    if no chroma_id or stale: re-embed → upsert vào ChromaDB.knowledge
For each kb in knowledge where is_active=False and chroma_id IS NOT NULL:
    Chroma.delete(chroma_id)
    set chroma_id = NULL
return (embedded_count, deactivated_count)
```

Dùng khi:
- Đổi embedding model (rebuild toàn bộ)
- ChromaDB bị mất / corrupt → restore từ MySQL
- Cron schedule mỗi sáng để đảm bảo consistency

### Soft delete (chưa expose CLI)

`deactivate_knowledge(id)` set `is_active=False` + `sync-knowledge` sẽ gỡ khỏi Chroma. Hiện chưa có CLI subcommand — cần SQL trực tiếp hoặc thêm command sau.

### Retrieve cho arbiter

[ai/memory.py](../../src/finance_bot/ai/memory.py) `retrieve_knowledge(query_text, n=4)`:

```
embedding = embed(query_text)
results = chroma.knowledge.query(embedding, n=4)
return [{title, body, tags, score}, ...]
```

`arbitrate()` dùng top-4 cho mỗi signal, đưa vào prompt block `knowledge_snippets`.

## 4.4 Validation rules

- `title` required; `body` required (nếu rỗng → CLI exit code 2).
- `tags` optional, comma-separated từ CLI, lưu JSON list trong DB.
- `chroma_id` UUID generate khi `learn_knowledge` lần đầu — không đổi.
- `is_active=False` → không xuất hiện trong RAG retrieve, vẫn còn trong MySQL (audit trail).

## 4.5 Edge cases

- **ChromaDB delete fail** (Chroma down): `sync-knowledge` log error, vẫn return; lần chạy sau retry.
- **Trùng title**: KHÔNG block — vì user có thể có nhiều ghi chú liên quan; dedup là trách nhiệm user.
- **Body cực dài (>5k token)**: embedding model truncate ở 256 tokens (MiniLM limit) — chỉ phần đầu được embed. User nên chia nhỏ.
- **Tag không tồn tại trong taxonomy**: không validate — RAG không filter by tag, chỉ cosine similarity.
- **Update body**: hiện chưa có CLI `update-knowledge`. Workaround: deactivate cũ + add mới. TODO cho M6.

## 4.6 CLI

```bash
add-knowledge --title "..." --body "..." [--body-file file.txt] [--tags a,b,c]
list-knowledge
sync-knowledge              # re-embed all active
rag-status                  # đếm document mỗi collection
```
