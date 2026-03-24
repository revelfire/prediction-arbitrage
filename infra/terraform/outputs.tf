output "droplet_ipv4" {
  description = "Public IPv4 address of the scanner droplet"
  value       = digitalocean_droplet.scanner.ipv4_address
}

output "droplet_ipv6" {
  description = "Public IPv6 address of the scanner droplet"
  value       = digitalocean_droplet.scanner.ipv6_address
}

output "ssh_command" {
  description = "SSH command to connect to the droplet"
  value       = "ssh root@${digitalocean_droplet.scanner.ipv4_address}"
}

output "volume_mount" {
  description = "Volume mount path on the droplet"
  value       = "/mnt/arb-scanner-pgdata"
}

output "post_provision_steps" {
  description = "Manual steps after terraform apply"
  value       = <<-EOT
    1. SSH into the droplet and wait for cloud-init to finish:
       ssh root@${digitalocean_droplet.scanner.ipv4_address}
       cloud-init status --wait

    2. Copy secrets to the droplet:
       scp .env root@${digitalocean_droplet.scanner.ipv4_address}:/opt/arb-scanner/.env
       scp config.yaml root@${digitalocean_droplet.scanner.ipv4_address}:/opt/arb-scanner/config.yaml
       scp kalshi_key.pem root@${digitalocean_droplet.scanner.ipv4_address}:/opt/arb-scanner/kalshi_key.pem

    3. Set permissions and start services:
       ssh root@${digitalocean_droplet.scanner.ipv4_address}
       chmod 600 /opt/arb-scanner/.env /opt/arb-scanner/kalshi_key.pem
       cd /opt/arb-scanner && docker compose -f docker-compose.prod.yml up -d

    4. Create deploy user for CI/CD:
       useradd -m -s /bin/bash deploy
       usermod -aG docker deploy
       mkdir -p /home/deploy/.ssh
       cp ~/.ssh/authorized_keys /home/deploy/.ssh/
       chown -R deploy:deploy /home/deploy/.ssh
       ln -s /opt/arb-scanner /home/deploy/arb-scanner

    5. Access dashboard:
       http://${digitalocean_droplet.scanner.ipv4_address}:8060
  EOT
}
