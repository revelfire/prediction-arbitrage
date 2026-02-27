variable "hcloud_token" {
  description = "Hetzner Cloud API token"
  type        = string
  sensitive   = true
}

variable "location" {
  description = "Hetzner datacenter location"
  type        = string
  default     = "nbg1"
}

variable "server_type" {
  description = "Hetzner server type (CX22 = 2 vCPU, 4 GB RAM)"
  type        = string
  default     = "cx22"
}

variable "ssh_public_key_path" {
  description = "Path to the SSH public key file for deploy access"
  type        = string
}

variable "allowed_ssh_cidrs" {
  description = "CIDR blocks allowed to SSH into the server"
  type        = list(string)
  default     = ["0.0.0.0/0", "::/0"]
}

variable "tailscale_auth_key" {
  description = "Tailscale pre-auth key for joining the tailnet"
  type        = string
  sensitive   = true
}

variable "ghcr_username" {
  description = "GitHub username for GHCR image pulls"
  type        = string
  default     = "revelfire"
}

variable "ghcr_token" {
  description = "GitHub PAT with read:packages scope for GHCR pulls"
  type        = string
  sensitive   = true
}
