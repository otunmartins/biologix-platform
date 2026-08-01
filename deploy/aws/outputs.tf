output "api_service_name" { value = aws_ecs_service.api.name }
output "worker_service_name" { value = aws_ecs_service.worker.name }
output "frontend_service_name" { value = aws_ecs_service.frontend.name }
output "artifact_bucket_name" { value = aws_s3_bucket.artifacts.id }
