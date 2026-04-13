# Attachments

The wrapper contract accepts attachment metadata and optional inline bytes. The
runtime never dereferences arbitrary URLs; the wrapper is responsible for
fetching platform files and sending either metadata only or bounded inline
content.

## Attachment shape

```json
{
  "platform_file_id": "F123",
  "filename": "notes.txt",
  "content_type": "text/plain",
  "size_bytes": 12,
  "content_base64": "aGVsbG8gd29ybGQK",
  "sha256_hex": "a948904f2f0f479b8f8197694b30184b0d2ed1c1cd2a1ec0fb85d299a192a447"
}
```

`content_base64` and `sha256_hex` are optional for metadata-only turns. Upload
requests require bytes.

## Limits

| Limit | Value |
|---|---|
| Attachments per turn | 10 |
| One attachment | 20 MiB |
| Total inline decoded bytes per turn | 20 MiB |
| Filename length | 255 characters |
| Content type | MIME-like `type/subtype` pattern |

## Upload command

The runtime handles this pattern directly:

```text
upload attached files to reports/
```

It decodes each attachment, checks declared size, verifies `sha256_hex` when
present, writes a temporary file under the session directory, uploads through
`CloudStorageClient.upload_file()`, and deletes the temp file in a `finally`
block.

Outcomes:

| Outcome | When |
|---|---|
| `reply` | Every attachment uploaded. |
| `partial_success` | At least one uploaded and at least one failed. |
| `error` | No attachment could be uploaded or storage is not configured. |

The remote path is `prefix.rstrip("/") + "/" + basename(filename)`.
