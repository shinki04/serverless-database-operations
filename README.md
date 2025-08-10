# Serverless Database Operations

## 1. Cài đặt thư viện cho Lambda Layer

Trước khi triển khai, cần cài đặt các thư viện Python vào thư mục layer để Lambda có thể sử dụng.
### Cấu trúc thư mục:
```
serverless-database-operations/
├── layer/
│   └── python/
│       └── lib/
│           └── python3.11/
│               └── site-packages/
│                   ├── mysql_connector_python/
│                   ├── redis/
│                   ├── requests/
├── cpu-scaler/
│   └── index.py
├── create-table/
│   └── index.py
├── insert-bulk/
│   └── index.py
├── crud-operations/
│   └── index.py
├── query-operations/
│   └── index.py
├── template.yaml
```
Chạy lệnh sau trong terminal:

```
pip install -t layer/python/lib/python3.11/site-packages/ requests mysql-connector-python redis
```
Hoặc
```
pip install -t layer/python/lib/python3.11/site-packages/ -r requirements.txt
```
> Lưu ý:
> 
> - `requests` dùng để gọi API.
> - `mysql-connector-python` dùng để kết nối MySQL (qua RDS hoặc Aurora).
> - `redis` dùng để kết nối ElastiCache hoặc Valkey.
> - Thư mục `layer/python/lib/python3.11/site-packages/` sẽ chứa các thư viện này, sau đó được zip và upload cùng Lambda Layer.

---

## 2. Triển khai với AWS SAM

Sau khi cài đặt thư viện và cấu hình dự án, thực hiện triển khai bằng lệnh:
```
sam build
```
```
sam validate
```
```
sam deploy \
  --stack-name ServerlessDatabaseOperations \
  --capabilities CAPABILITY_IAM CAPABILITY_NAMED_IAM \
  --region ap-southeast-1 \
  --resolve-s3

```

> Tham số giải thích:
> 
> - `-stack-name`: Tên stack CloudFormation (ở đây là **ServerlessDatabaseOperations**).
> - `-capabilities`: Cho phép SAM tạo/ghi đè IAM Roles.
> - `-region`: Khu vực AWS triển khai (Singapore `ap-southeast-1`).
> - `-resolve-s3`: Tự động tạo bucket S3 để upload code.