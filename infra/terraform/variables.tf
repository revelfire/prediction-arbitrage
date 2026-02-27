variable "do_token" {
  description = "DigitalOcean API token"
  type        = string
  sensitive   = true
}

variable "region" {
  description = "DigitalOcean region (nyc1, sfo3, ams3, etc.)"
  type        = string
  default     = "nyc1"
}

variable "droplet_size" {
  description = "Droplet size slug (s-2vcpu-4gb = 2 vCPU, 4 GB RAM, $24/mo)"
  type        = string
  default     = "s-2vcpu-4gb"
}

variable "ssh_public_key_path" {
  description = "Path to the SSH public key file for deploy access"
  type        = string
}

variable "allowed_ssh_cidrs" {
  description = "CIDR blocks allowed to SSH into the droplet"
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

variable "spaces_access_key" {
  description = "DigitalOcean Spaces access key for backups"
  type        = string
  sensitive   = true
  default     = ""
}

variable "spaces_secret_key" {
  description = "DigitalOcean Spaces secret key for backups"
  type        = string
  sensitive   = true
  default     = ""
}

variable "spaces_region" {
  description = "DigitalOcean Spaces region for backup bucket"
  type        = string
  default     = "nyc3"
}

variable "expressvpn_activation_code" {
  description = "ExpressVPN activation code for geo-routing through Mexico"
  type        = string
  sensitive   = true
}

variable "expressvpn_location" {
  description = "ExpressVPN server location to connect to"
  type        = string
  default     = "Mexico"
}
