
from generation.frontend_api_analyzer import analyze_frontend_api_requirements


def test_analyzer_detects_fetch_and_axios_calls(tmp_path):
    src = tmp_path / "src"
    src.mkdir()

    (src / "App.jsx").write_text(
        """
        async function loadProducts() {
            return fetch('/api/products');
        }

        async function createProduct(data) {
            return fetch('/api/products', {
                method: 'POST',
                body: JSON.stringify(data)
            });
        }

        async function loadUsers() {
            return axios.get('/api/users');
        }

        async function deleteUser(id) {
            return axios.delete('/api/users');
        }
        """,
        encoding="utf-8",
    )

    requirements = analyze_frontend_api_requirements(tmp_path)

    detected = {
        (item["method"], item["path"])
        for item in requirements
    }

    assert ("GET", "/api/products") in detected
    assert ("POST", "/api/products") in detected
    assert ("GET", "/api/users") in detected
    assert ("DELETE", "/api/users") in detected


def test_analyzer_ignores_external_urls(tmp_path):
    (tmp_path / "App.jsx").write_text(
        "fetch('https://example.com/api/products');",
        encoding="utf-8",
    )

    assert analyze_frontend_api_requirements(tmp_path) == []



def test_analyzer_extracts_static_json_body_fields(tmp_path):
    (tmp_path / "App.jsx").write_text(
        """
        async function createProduct() {
            return fetch('/api/products', {
                method: 'POST',
                body: JSON.stringify({
                    name: 'Chocolate Cake',
                    price: 250
                })
            });
        }
        """,
        encoding="utf-8",
    )

    requirements = analyze_frontend_api_requirements(tmp_path)
    post_requirement = next(
        item for item in requirements
        if item["method"] == "POST"
    )

    assert set(post_requirement["request_fields"]) == {"name", "price"}
