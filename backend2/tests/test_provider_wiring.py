from pathlib import Path

from generation.frontend_generator import _find_error_files
from generation.provider_wiring import (
    build_main_jsx,
    repair_project_main,
    validate_provider_wiring,
)

CONTEXT = """import { createContext, useContext, useState } from 'react';
const GalleryContext = createContext(null);
export function GalleryProvider({ children }) {
  const [items] = useState([]);
  return <GalleryContext.Provider value={{ items }}>{children}</GalleryContext.Provider>;
}
export function useGallery() {
  const ctx = useContext(GalleryContext);
  if (!ctx) throw new Error('useGallery must be used within a GalleryProvider');
  return ctx;
}
"""
PAGE = "import { useGallery } from '../context/GalleryContext.jsx';\nexport default function Gallery() { const { items } = useGallery(); return <div>{items.length}</div>; }\n"
APP = "export default function App() { return <div />; }\n"


def _project(tmp_path: Path) -> Path:
    for rel, text in {
        "src/context/GalleryContext.jsx": CONTEXT,
        "src/pages/Gallery.jsx": PAGE,
        "src/App.jsx": APP,
    }.items():
        f = tmp_path / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text, encoding="utf-8")
    return tmp_path


def test_unmounted_provider_is_detected(tmp_path):
    project = _project(tmp_path)
    assert any("GalleryProvider" in p for p in validate_provider_wiring(project))


def test_main_jsx_mounts_missing_provider(tmp_path):
    project = _project(tmp_path)
    assert repair_project_main(project) == ["GalleryProvider"]
    main = (project / "src/main.jsx").read_text(encoding="utf-8")
    assert "<GalleryProvider>" in main and "data-agentforge-crash" in main
    assert validate_provider_wiring(project) == []


def test_provider_already_in_app_is_not_mounted_twice(tmp_path):
    project = _project(tmp_path)
    (project / "src/App.jsx").write_text(
        "import { GalleryProvider } from './context/GalleryContext.jsx';\n"
        "export default function App() { return <GalleryProvider><div /></GalleryProvider>; }\n",
        encoding="utf-8",
    )
    assert "<GalleryProvider>" not in build_main_jsx(project)


def test_error_text_maps_to_files():
    manifest = [
        {"path": "src/pages/Gallery.jsx", "default_export": "Gallery", "named_exports": []},
        {"path": "src/context/GalleryContext.jsx", "default_export": None,
         "named_exports": ["GalleryProvider", "useGallery"]},
    ]
    esbuild = 'D:\\x\\frontend\\src\\pages\\Gallery.jsx:25:4: ERROR: Unexpected "}"'
    assert _find_error_files(esbuild, manifest) == ["src/pages/Gallery.jsx"]
    stack = "Error: useGallery must be used within a GalleryProvider\n    at Gallery (http://x/a.js:1:1)"
    found = _find_error_files(stack, manifest)
    assert "src/pages/Gallery.jsx" in found and "src/context/GalleryContext.jsx" in found


def test_property_mismatch_in_multi_provider_file(tmp_path):
    from generation.provider_wiring import validate_context_properties

    store = """import { createContext, useContext, useState } from 'react';
const ServiceContext = createContext(null);
export function ServiceProvider({ children }) {
  const fetchServices = async () => { const r = await fetch('/api/services'); return r.json(); };
  return (
    <ServiceContext.Provider value={{ services: [], loading: false, error: null, refetch: fetchServices }}>
      {children}
    </ServiceContext.Provider>
  );
}
export function useService() { return useContext(ServiceContext); }
const GalleryContext = createContext(null);
export function GalleryProvider({ children }) {
  return <GalleryContext.Provider value={{ images: [] }}>{children}</GalleryContext.Provider>;
}
export function useGallery() { return useContext(GalleryContext); }
"""
    page = ("import { useService } from '../context/StoreContext.jsx';\n"
            "export default function Services() {\n"
            "  const { services, loading, error, fetchServices } = useService();\n"
            "  return <div />;\n}\n")
    ok_page = ("import { useGallery } from '../context/StoreContext.jsx';\n"
               "export default function Gallery() { const { images } = useGallery(); return <div />; }\n")
    for rel, text in {
        "src/context/StoreContext.jsx": store,
        "src/pages/Services.jsx": page,
        "src/pages/Gallery.jsx": ok_page,
    }.items():
        f = tmp_path / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text, encoding="utf-8")

    found = validate_context_properties(tmp_path)
    assert list(found) == ["src/pages/Services.jsx"]
    assert "fetchServices" in found["src/pages/Services.jsx"][0]
    assert "refetch" in found["src/pages/Services.jsx"][0]


def test_component_prop_name_mismatch(tmp_path):
    from generation.component_contracts import validate_component_props

    files = {
        "src/components/ServiceCard.jsx":
            "export default function ServiceCard({ service }) { return <h3>{service.name}</h3>; }\n",
        "src/pages/Services.jsx":
            "import ServiceCard from '../components/ServiceCard.jsx';\n"
            "export default function Services({ list }) {\n"
            "  return list.map((s) => <ServiceCard key={s.id} id={s.id} name={s.name} price={s.price} />);\n}\n",
        "src/components/Fine.jsx":
            "export default function Fine({ title, className = '' }) { return <p className={className}>{title}</p>; }\n",
        "src/pages/UsesFine.jsx":
            "import Fine from '../components/Fine.jsx';\n"
            "export default function U() { return <Fine title=\"a\" className=\"x\" data-x=\"1\" />; }\n",
    }
    for rel, text in files.items():
        f = tmp_path / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text, encoding="utf-8")
    found = validate_component_props(tmp_path)
    assert list(found) == ["src/pages/Services.jsx"]
    assert "service" in found["src/pages/Services.jsx"][0]
