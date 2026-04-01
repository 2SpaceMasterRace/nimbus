# Beginner Curl Tutorial

This walkthrough shows how to upload, list, download, and delete a file through the FastAPI service using `curl`.

## What You Need

- A running service, either local or deployed.
- An API key in `API_KEY`.
- The configured S3 bucket name in `AWS_BUCKET_NAME`.
- A small local file to upload.

## 1. Set your shell variables

```console
$ export BASE_URL="https://ospsd-team-2.fly.dev"
$ export API_KEY="replace-me"
$ export AWS_BUCKET_NAME="your-bucket-name"
```

If you are running the service locally, set `BASE_URL="http://localhost:8000"` instead.

## 2. Create a dummy file

Use any text file you like. Here is a tiny example:

```console
$ printf 'hello from curl\n' > curl-demo.txt
```

## 3. Upload the file

This sends a multipart form request to the upload endpoint.

```console
$ curl -X POST "$BASE_URL/files/$AWS_BUCKET_NAME/tutorial/curl-demo.txt" \
    -H "X-API-Key: $API_KEY" \
    -F "file=@curl-demo.txt"
```

Expected response:

```json
{"ok":true}
```

## 4. List matching files

This confirms that the object exists in the bucket.

```console
$ curl "$BASE_URL/files?prefix=tutorial/" \
    -H "X-API-Key: $API_KEY"
```

Expected response:

```json
{"files":["tutorial/curl-demo.txt"]}
```

## 5. Download the file

This saves the response body into a new local file.

```console
$ curl "$BASE_URL/download?bucket_name=$AWS_BUCKET_NAME&object_name=tutorial/curl-demo.txt" \
    -H "X-API-Key: $API_KEY" \
    --output curl-demo-downloaded.txt
```

You can compare the files:

```console
$ cmp curl-demo.txt curl-demo-downloaded.txt
```

No output means the files are identical.

## 6. Delete the file

```console
$ curl -X DELETE "$BASE_URL/files/$AWS_BUCKET_NAME/tutorial/curl-demo.txt" \
    -H "X-API-Key: $API_KEY"
```

Expected response:

```json
{"ok":true}
```

## 7. Confirm cleanup

Run the list command again:

```console
$ curl "$BASE_URL/files?prefix=tutorial/" \
    -H "X-API-Key: $API_KEY"
```

Expected response:

```json
{"files":[]}
```

## Common Mistakes

- Wrong bucket name: the service rejects upload, download, and delete if the request bucket does not match the configured `AWS_BUCKET_NAME`.
- Missing API key: protected endpoints return `401 Authentication required`.
- Wrong object key: download or delete returns `404` when the object does not exist.
- Missing `@` in `-F "file=@curl-demo.txt"`: curl sends the filename string instead of the file contents.

## Live Deployment

The Fly deployment serves this Sphinx site directly from the application image.

- Service root: `https://ospsd-team-2.fly.dev/`
- Health check: `https://ospsd-team-2.fly.dev/health`
- OpenAPI schema: `https://ospsd-team-2.fly.dev/openapi.json`
- Sphinx guide: `https://ospsd-team-2.fly.dev/guide/`
