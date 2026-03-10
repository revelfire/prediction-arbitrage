# --- SSH Key ---

resource "digitalocean_ssh_key" "deploy" {
  name       = "arb-scanner-deploy"
  public_key = file(var.ssh_public_key_path)
}

# --- Firewall ---

resource "digitalocean_firewall" "scanner" {
  name = "arb-scanner"

  droplet_ids = [digitalocean_droplet.scanner.id]

  # SSH from allowed CIDRs
  inbound_rule {
    protocol         = "tcp"
    port_range       = "22"
    source_addresses = var.allowed_ssh_cidrs
  }

  # Tailscale WireGuard (UDP 41641) from anywhere — Tailscale handles its own auth
  inbound_rule {
    protocol         = "udp"
    port_range       = "41641"
    source_addresses = ["0.0.0.0/0", "::/0"]
  }

  # All outbound traffic (API calls, GHCR pulls, etc.)
  outbound_rule {
    protocol              = "tcp"
    port_range            = "1-65535"
    destination_addresses = ["0.0.0.0/0", "::/0"]
  }

  outbound_rule {
    protocol              = "udp"
    port_range            = "1-65535"
    destination_addresses = ["0.0.0.0/0", "::/0"]
  }

  outbound_rule {
    protocol              = "icmp"
    destination_addresses = ["0.0.0.0/0", "::/0"]
  }
}

# --- Persistent Volume for PostgreSQL ---

resource "digitalocean_volume" "pgdata" {
  name                    = "arb-scanner-pgdata"
  region                  = var.region
  size                    = 20
  initial_filesystem_type = "ext4"
  description             = "PostgreSQL data for arb-scanner"
}

# --- Droplet ---

resource "digitalocean_droplet" "scanner" {
  name     = "arb-scanner"
  size     = var.droplet_size
  region   = var.region
  image    = "ubuntu-24-04-x64"
  ssh_keys = [digitalocean_ssh_key.deploy.fingerprint]

  user_data = templatefile("${path.module}/cloud-init.yml", {
    tailscale_auth_key         = var.tailscale_auth_key
    ghcr_username              = var.ghcr_username
    ghcr_token                 = var.ghcr_token
    spaces_access_key          = var.spaces_access_key
    spaces_secret_key          = var.spaces_secret_key
    spaces_region              = var.spaces_region
    expressvpn_activation_code = var.expressvpn_activation_code
    expressvpn_location        = var.expressvpn_location
  })

  volume_ids = [digitalocean_volume.pgdata.id]
}

# --- Spaces Bucket for Backups (optional — skip if spaces keys not set) ---

resource "digitalocean_spaces_bucket" "backups" {
  count  = var.spaces_access_key != "" ? 1 : 0
  name   = "arb-scanner-backups"
  region = var.spaces_region
}
