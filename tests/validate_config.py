"""
Validate Docker configuration files for correctness.
Checks docker-compose.yml, Dockerfiles, and nginx.conf.
"""

import os
import sys


def validate_docker_compose():
    """Validate docker-compose.yml structure."""
    print("=" * 60)
    print("Validating docker-compose.yml")
    print("=" * 60)

    try:
        import yaml
    except ImportError:
        # Fall back to basic file reading
        with open("docker-compose.yml", "r") as f:
            content = f.read()
        # Basic checks
        checks = [
            ("services:", "Services section"),
            ("db:", "Database service"),
            ("api:", "API service"),
            ("optimizer:", "Optimizer service"),
            ("client:", "Client service"),
            ("postgres:16", "PostgreSQL 16 image"),
            ("nginx:1.27", "Nginx image"),
            ("depends_on:", "Service dependencies"),
            ("healthcheck:", "Health checks"),
            ("condition: service_healthy", "Conditional startup"),
            ("volumes:", "Volumes section"),
            ("networks:", "Networks section"),
            ("USER 1000", "Non-root user"),
            ("routeweave-net", "Custom network"),
            ("pgdata", "Persistent volume"),
        ]

        for pattern, desc in checks:
            if pattern in content:
                print(f"  [OK] {desc}")
            else:
                print(f"  [!!] Missing: {desc}")

        return True

    with open("docker-compose.yml", "r") as f:
        data = yaml.safe_load(f)

    services = data.get("services", {})
    print(f"  Services: {list(services.keys())}")
    print(f"  Volumes: {list(data.get('volumes', {}).keys())}")
    print(f"  Networks: {list(data.get('networks', {}).keys())}")

    for name, svc in services.items():
        deps = svc.get("depends_on", "none")
        print(f"  {name}: depends_on={deps}")

    return True


def validate_dockerfiles():
    """Validate Dockerfile best practices."""
    print("\n" + "=" * 60)
    print("Validating Dockerfiles")
    print("=" * 60)

    dockerfiles = [
        "api/Dockerfile",
        "optimizer/Dockerfile",
    ]

    security_checks = [
        ("USER ", "Non-root user"),
        ("HEALTHCHECK", "Health check"),
        ("--no-cache-dir", "No pip cache"),
        ("python:3.12-slim", "Slim base image"),
        ("COPY requirements.txt", "Layer-cached dependencies"),
        ("LABEL", "Container labels"),
    ]

    for df_path in dockerfiles:
        if not os.path.exists(df_path):
            print(f"  [!!] Missing: {df_path}")
            continue

        with open(df_path, "r") as f:
            content = f.read()

        print(f"\n  {df_path}:")
        for pattern, desc in security_checks:
            if pattern in content:
                print(f"    [OK] {desc}")
            else:
                print(f"    [!!] Missing: {desc}")


def validate_nginx():
    """Validate nginx.conf security headers."""
    print("\n" + "=" * 60)
    print("Validating nginx.conf")
    print("=" * 60)

    nginx_path = "client/nginx.conf"
    if not os.path.exists(nginx_path):
        print(f"  [!!] Missing: {nginx_path}")
        return

    with open(nginx_path, "r") as f:
        content = f.read()

    headers = [
        ("X-Content-Type-Options", "Content-Type nosniff"),
        ("X-Frame-Options", "Clickjacking protection"),
        ("X-XSS-Protection", "XSS protection"),
        ("Content-Security-Policy", "CSP header"),
        ("Referrer-Policy", "Referrer policy"),
        ("proxy_pass", "API proxy"),
        ("gzip on", "Gzip compression"),
        ("/\\.", "Hidden file blocking"),
    ]

    for pattern, desc in headers:
        if pattern in content:
            print(f"  [OK] {desc}")
        else:
            print(f"  [!!] Missing: {desc}")


def validate_gitignore():
    """Validate .gitignore covers secrets."""
    print("\n" + "=" * 60)
    print("Validating .gitignore")
    print("=" * 60)

    with open(".gitignore", "r") as f:
        content = f.read()

    patterns = [
        (".env", "Environment secrets"),
        ("__pycache__", "Python cache"),
        (".pytest_cache", "Pytest cache"),
        ("pgdata", "Database volume"),
        (".venv", "Virtual environment"),
    ]

    for pattern, desc in patterns:
        if pattern in content:
            print(f"  [OK] {desc}")
        else:
            print(f"  [!!] Missing: {desc}")

    # Verify .env is not tracked
    if os.path.exists(".env"):
        print(f"  [OK] .env file exists locally (for development)")
    if os.path.exists(".env.example"):
        print(f"  [OK] .env.example template exists")


def validate_ci():
    """Validate CI/CD pipeline configuration."""
    print("\n" + "=" * 60)
    print("Validating CI/CD pipeline")
    print("=" * 60)

    ci_path = ".github/workflows/ci.yml"
    if not os.path.exists(ci_path):
        print(f"  [!!] Missing: {ci_path}")
        return

    with open(ci_path, "r", encoding="utf-8") as f:
        content = f.read()

    checks = [
        ("test:", "Test job"),
        ("security-scan:", "Security scan job"),
        ("build-and-push:", "Build & push job"),
        ("deploy:", "Deploy job"),
        ("needs: test", "Build needs test"),
        ("needs: [test, security-scan]", "Build needs security scan"),
        ("needs: build-and-push", "Deploy needs build"),
        ("trivy", "Trivy vulnerability scanner"),
        ("ghcr.io", "GitHub Container Registry"),
        ("GITHUB_TOKEN", "GHCR authentication"),
        ("pytest", "Runs pytest"),
    ]

    for pattern, desc in checks:
        if pattern in content:
            print(f"  [OK] {desc}")
        else:
            print(f"  [!!] Missing: {desc}")


def validate_project_structure():
    """Validate the full project structure."""
    print("\n" + "=" * 60)
    print("Validating project structure")
    print("=" * 60)

    expected_files = [
        "api/main.py",
        "api/models.py",
        "api/database.py",
        "api/geocode.py",
        "api/requirements.txt",
        "api/Dockerfile",
        "optimizer/haversine.py",
        "optimizer/optimize.py",
        "optimizer/server.py",
        "optimizer/requirements.txt",
        "optimizer/Dockerfile",
        "client/index.html",
        "client/styles.css",
        "client/map.js",
        "client/nginx.conf",
        "db/init.sql",
        "tests/conftest.py",
        "tests/test_optimizer.py",
        "tests/test_api.py",
        "tests/test_integration.py",
        "docker-compose.yml",
        ".env.example",
        ".gitignore",
        ".github/workflows/ci.yml",
        "pytest.ini",
        "README.md",
        "sample_addresses.csv",
    ]

    for path in expected_files:
        if os.path.exists(path):
            size = os.path.getsize(path)
            print(f"  [OK] {path} ({size:,} bytes)")
        else:
            print(f"  [!!] MISSING: {path}")


if __name__ == "__main__":
    validate_project_structure()
    validate_docker_compose()
    validate_dockerfiles()
    validate_nginx()
    validate_gitignore()
    validate_ci()

    print("\n" + "=" * 60)
    print("VALIDATION COMPLETE")
    print("=" * 60)
