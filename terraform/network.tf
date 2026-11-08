# ------------------------------------------------------------
# VPC / サブネット
# ------------------------------------------------------------
resource "google_compute_network" "vpc" {
  name                    = "vpc-${var.environment}"
  auto_create_subnetworks = false

  depends_on = [google_project_service.apis]
}

resource "google_compute_subnetwork" "subnet" {
  name          = "subnet-${var.environment}"
  ip_cidr_range = "10.10.0.0/24"
  region        = var.region
  network       = google_compute_network.vpc.id

  # GCEからPub/Sub・Secret Manager等のGoogle APIへ、外部IPなしで到達するために有効化
  private_ip_google_access = true
}

# ------------------------------------------------------------
# Cloud SQL用のプライベートサービス接続（Private Service Access）
# ------------------------------------------------------------
resource "google_compute_global_address" "private_ip_alloc" {
  name          = "psa-cloudsql-${var.environment}"
  purpose       = "VPC_PEERING"
  address_type  = "INTERNAL"
  prefix_length = 16
  network       = google_compute_network.vpc.id
}

resource "google_service_networking_connection" "private_vpc_connection" {
  network                 = google_compute_network.vpc.id
  service                 = "servicenetworking.googleapis.com"
  reserved_peering_ranges = [google_compute_global_address.private_ip_alloc.name]

  depends_on = [google_project_service.apis]
}

# ------------------------------------------------------------
# Cloud NAT（GCEに外部IPを付けずに、Grafana Cloudへの送信など外部通信を可能にする）
# ------------------------------------------------------------
resource "google_compute_router" "router" {
  name    = "router-${var.environment}"
  region  = var.region
  network = google_compute_network.vpc.id
}

resource "google_compute_router_nat" "nat" {
  name                               = "nat-${var.environment}"
  router                             = google_compute_router.router.name
  region                             = var.region
  nat_ip_allocate_option             = "AUTO_ONLY"
  source_subnetwork_ip_ranges_to_nat = "ALL_SUBNETWORKS_ALL_IP_RANGES"
}

# ------------------------------------------------------------
# ファイアウォール
# ------------------------------------------------------------

# VPC内部の通信を許可（Cloud Run JobのVPCアクセス、GCE間通信など）
resource "google_compute_firewall" "allow_internal" {
  name    = "allow-internal-${var.environment}"
  network = google_compute_network.vpc.id

  allow {
    protocol = "tcp"
    ports    = ["0-65535"]
  }
  allow {
    protocol = "udp"
    ports    = ["0-65535"]
  }
  allow {
    protocol = "icmp"
  }

  source_ranges = ["10.10.0.0/24"]
}

# IAP経由のSSHのみ許可（外部IPなしでもgcloud compute ssh --tunnel-through-iapで接続可能にする）
resource "google_compute_firewall" "allow_iap_ssh" {
  name    = "allow-iap-ssh-${var.environment}"
  network = google_compute_network.vpc.id

  allow {
    protocol = "tcp"
    ports    = ["22"]
  }

  # GoogleのIAP専用IP範囲
  source_ranges = ["35.235.240.0/20"]
  target_tags   = ["otel-collector"]
}
