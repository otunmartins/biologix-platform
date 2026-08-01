variable "aws_region" {
  type    = string
  default = "us-east-1"
}
variable "environment" {
  type    = string
  default = "production"
}
variable "cluster_arn" { type = string }
variable "subnet_ids" { type = list(string) }
variable "security_group_ids" { type = list(string) }
variable "api_target_group_arn" { type = string }
variable "frontend_target_group_arn" { type = string }
variable "api_image" { type = string }
variable "worker_image" { type = string }
variable "frontend_image" { type = string }
variable "database_url_secret_arn" { type = string }
variable "redis_url_secret_arn" { type = string }
variable "session_secret_arn" { type = string }
variable "api_desired_count" {
  type    = number
  default = 2
}
variable "worker_desired_count" {
  type    = number
  default = 1
}
variable "frontend_desired_count" {
  type    = number
  default = 2
}
