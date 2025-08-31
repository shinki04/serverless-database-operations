# Serverless Database Operations with AWS
[![FCJ](https://img.shields.io/badge/FCJ-Internship-blue)](https://github.com/AWS-First-Cloud-Journey/Internship)
[![AWS](https://img.shields.io/badge/AWS-Cloud-orange)](https://aws.amazon.com/)

# Tổng quan dự án

**Serverless Database Operations** là dự án minh họa cách thực hiện các hoạt động cơ sở dữ liệu không máy chủ (serverless) sử dụng các dịch vụ AWS như Lambda, RDS Proxy, ElastiCache và Lambda Layers. Dự án bao gồm các hàm Lambda để scale CPU, tạo bảng, chèn dữ liệu hàng loạt, thực hiện CRUD và truy vấn dữ liệu.

Dự án này phù hợp cho những người học AWS, muốn xây dựng kiến thức về kiến trúc serverless và tích hợp cơ sở dữ liệu.

### Mục tiêu dự án

- Hiểu và triển khai kết nối cơ sở dữ liệu serverless với RDS Proxy và ElastiCache.
- Sử dụng Lambda Layers để quản lý thư viện phụ thuộc.
- Thực hiện các hoạt động cơ bản trên cơ sở dữ liệu qua Lambda functions.
- Xây dựng và deploy ứng dụng serverless với AWS SAM.
- Phát triển kỹ năng DevOps trên AWS.

## Các thành phần chính

### 1. Lambda Layers cho thư viện

- **Yêu cầu**: Cài đặt thư viện Python vào thư mục layer để Lambda sử dụng.
- **Trọng tâm**: Bao gồm requests, mysql-connector-python, redis.
- **Chi tiết**: Xem hướng dẫn cài đặt bên dưới.

### 2. Các hàm Lambda

- **Yêu cầu**: Các hàm xử lý hoạt động cơ sở dữ liệu.
- **Trọng tâm**: CPU scaling, tạo bảng, chèn bulk, CRUD, truy vấn.
- **Chi tiết**: Mỗi hàm nằm trong thư mục riêng với file index.py.

### 3. Triển khai với AWS SAM

- **Yêu cầu**: Sử dụng template.yaml để deploy stack CloudFormation.
- **Trọng tâm**: Build và deploy tự động.
- **Chi tiết**: Xem hướng dẫn deploy.

# Cấu trúc Repository

```
📦 serverless-database-operations/
├── 📁 layer/                         # Thư mục cho Lambda Layer
│   └── 📁 python/
│       └── 📁 lib/
│           └── 📁 python3.11/
│               └── 📁 site-packages/ # Thư viện được cài đặt
│                   ├── mysql_connector_python/
│                   ├── redis/
│                   ├── requests/
├── 📁 cpu-scaler/                    # Hàm scale CPU
│   └── 📄 index.py
├── 📁 create-table/                  # Hàm tạo bảng
│   └── 📄 index.py
├── 📁 insert-bulk/                   # Hàm chèn dữ liệu bulk
│   └── 📄 index.py
├── 📁 crud-operations/               # Hàm CRUD
│   └── 📄 index.py
├── 📁 query-operations/              # Hàm truy vấn
│   └── 📄 index.py
├── 📄 template.yaml                  # Template AWS SAM
├── 📄 requirements.txt               # Danh sách thư viện
├── 📄 .gitignore                     # File ignore Git
└── 📄 README.md                      # File này - Hướng dẫn dự án
```

# Hướng dẫn bắt đầu

## **Bước 1: Clone Repository**

```bash
# Clone repo về máy local
git clone https://github.com/shinki04/serverless-database-operations.git
cd serverless-database-operations
```

## **Bước 2: Cài đặt thư viện cho Lambda Layer**

```bash
# Cài đặt trực tiếp
pip install -t layer/python/lib/python3.11/site-packages/ requests mysql-connector-python redis

# Hoặc sử dụng requirements.txt
pip install -t layer/python/lib/python3.11/site-packages/ -r layer/requirements.txt

```

Lưu ý:

- `requests`: Dùng cho gọi API.
- `mysql-connector-python`: Kết nối MySQL (qua RDS/Aurora).
- `redis`: Kết nối ElastiCache hoặc Valkey.
- Thư mục layer sẽ được zip và upload làm Lambda Layer.

## **Bước 3: Build dự án với AWS SAM**

> Cài AWS SAM nếu chưa có: https://docs.aws.amazon.com/serverless-application-model/latest/developerguide/install-sam-cli.html
> 

```bash
sam build
sam validate
```

## **Bước 4: Triển khai**

Khuyến nghị sử dụng guided mode cho lần đầu:

```bash
sam deploy --guided
```

Hoặc deploy trực tiếp:

```bash
sam deploy \
  --stack-name ServerlessDatabaseOperations \
  --capabilities CAPABILITY_IAM CAPABILITY_NAMED_IAM \
  --region ap-southeast-1 \
  --resolve-s3
```

Giải thích tham số:

- -stack-name: Tên stack CloudFormation.
- -capabilities: Cho phép tạo IAM Roles.
- -region: Khu vực AWS (ví dụ: ap-southeast-1 - Singapore).
- -resolve-s3: Tạo bucket S3 tự động để upload code.

# Dịch vụ AWS sử dụng

| Dịch vụ | Mô tả | Vai trò trong dự án |
| --- | --- | --- |
| Amazon VPC | (Virtual Private Cloud) Tạo ra một mạng ảo riêng biệt và cô lập cho các tài nguyên AWS. | Cung cấp môi trường mạng nền tảng, đảm bảo các tài nguyên như RDS, ElastiCache, và Lambda có thể giao tiếp với nhau một cách an toàn và riêng tư, không bị truy cập từ internet công cộng. |
| Amazon EC2 (Security Group) | Hoạt động như một tường lửa ảo để kiểm soát lưu lượng truy cập vào và ra cho các tài nguyên. | Định nghĩa các quy tắc truy cập giữa các thành phần: Lambda có thể nói chuyện với RDS Proxy và ElastiCache, RDS Proxy có thể nói chuyện với RDS, v.v. |
| AWS IAM | (Identity and Access Management) Quản lý quyền truy cập vào các dịch vụ và tài nguyên AWS một cách an toàn. | Cung cấp các vai trò (Roles) và chính sách (Policies) cần thiết để Lambda, RDS Proxy có đủ quyền thực thi các hành động như kết nối cơ sở dữ liệu, đọc secret, ghi logs. |
| Amazon RDS & RDS Proxy | (Relational Database Service) Cung cấp một cơ sở dữ liệu quan hệ (MySQL) được quản lý. RDS Proxy quản lý các kết nối đến cơ sở dữ liệu. | RDS: Lưu trữ dữ liệu chính của ứng dụng. <br> RDS Proxy: Tối ưu hóa việc quản lý kết nối từ Lambda, giúp tăng hiệu suất và khả năng phục hồi của ứng dụng serverless. |
| Amazon ElastiCache (Valkey) | Dịch vụ cache trong bộ nhớ (in-memory cache) được quản lý, sử dụng engine Valkey (tương thích Redis). | Tăng tốc độ truy vấn bằng cách lưu trữ các dữ liệu thường xuyên truy cập vào bộ nhớ đệm, giảm tải cho cơ sở dữ liệu RDS chính. |
| AWS Lambda | Dịch vụ tính toán serverless cho phép chạy code mà không cần quản lý máy chủ. | Cung cấp logic nghiệp vụ cho ứng dụng, bao gồm: tạo bảng, thực hiện các thao tác CRUD, truy vấn dữ liệu, và tự động co giãn (scaling) RDS instance. |
| Amazon API Gateway | Dịch vụ được quản lý hoàn toàn để tạo, xuất bản, duy trì, giám sát và bảo mật các API. | Tạo ra các điểm cuối HTTP (endpoints) để thế giới bên ngoài có thể tương tác với các hàm Lambda, kích hoạt logic nghiệp vụ của ứng dụng. |
| AWS Lambda Layer | Một cơ chế để đóng gói và chia sẻ các thư viện hoặc các dependency khác giữa nhiều hàm Lambda. | Chứa các thư viện Python cần thiết (như mysql-connector-python, redis) để các hàm Lambda có thể kết nối và tương tác với RDS và ElastiCache. |
| AWS Secrets Manager | Dịch vụ giúp bảo vệ các thông tin bí mật cần thiết để truy cập ứng dụng, dịch vụ và tài nguyên CNTT của bạn. | Lưu trữ an toàn thông tin đăng nhập (username/password) của cơ sở dữ liệu RDS, giúp tránh việc hard-code các thông tin nhạy cảm này trong code. |
| Amazon CloudWatch | Dịch vụ giám sát và quản lý cho các tài nguyên AWS và các ứng dụng chạy trên AWS. | Giám sát chỉ số CPU của RDS Instance. Khi CPU vượt ngưỡng cao hoặc thấp, nó sẽ kích hoạt các cảnh báo (Alarms). |
| Amazon SNS | (Simple Notification Service) Dịch vụ nhắn tin và thông báo được quản lý hoàn toàn. | Đóng vai trò là trung gian, nhận thông báo từ CloudWatch Alarms và chuyển tiếp chúng đến hàm Lambda ServerlessDBCPUScaler để xử lý. |

# Quy trình test và debug

- **Invoke hàm**: Sử dụng AWS Console hoặc CLI để test từng hàm Lambda.
- **Logs**: Kiểm tra CloudWatch Logs cho lỗi.
- **Debug**: Thêm print statements trong code và rebuild.
- **Cleanup**: Xóa stack sau khi test: sam delete --stack-name ServerlessDatabaseOperations.

# Lợi ích khi sử dụng dự án

### Immediate Benefits

- **Kiến thức thực tế**: Học serverless architecture trên AWS.
- **Portfolio**: Dự án có thể thêm vào CV hoặc GitHub.

### Career Benefits

- **Kỹ năng AWS**: Chuẩn bị cho certifications như AWS Developer Associate.
- **Networking**: Tham gia cộng đồng AWS Việt Nam.

### Learning Benefits

- **Tài liệu**: Hiểu sâu về RDS Proxy và ElastiCache.
- **Workshop**: Áp dụng vào các dự án lớn hơn.

# **🤝 Contributing**

Hoan nghênh contributions để cải thiện dự án:

1. **Fork** repo này.
2. **Create** branch (`git checkout -b feature/new-function`).
3. **Commit** changes (`git commit -am 'Add new function'`).
4. **Push** (`git push origin feature/new-function`).
5. **Create** Pull Request.

### Contribution Guidelines

- Thêm hàm mới hoặc cải thiện code.
- Cập nhật documentation.
- Sửa lỗi và thêm tests.

# **📚 Tài liệu tham khảo**

### AWS Official

- [AWS Lambda Documentation](https://docs.aws.amazon.com/lambda/)
- [RDS Proxy](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/rds-proxy.html)
- [ElastiCache](https://aws.amazon.com/elasticache/)
- [AWS SAM](https://docs.aws.amazon.com/serverless-application-model/)
- [AWS CloudFormation](https://docs.aws.amazon.com/AWSCloudFormation/latest/UserGuide/Welcome.html)
### Additional Resources

- [AWS Training](https://aws.amazon.com/training/)
- [AWS Well-Architected Framework](https://aws.amazon.com/architecture/well-architected/)

---

*Cập nhật lần cuối: 2025-09-01*

*Phiên bản: 1.1.0*