from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class FileType:
    key: str
    icon: str
    extensions: tuple[str, ...] = ()
    language: str | None = None


FILES = (
    FileType("python", "fa5b.python", (".py", ".pyw"), "python"),
    FileType("javascript", "fa5b.js", (".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx"), "js"),
    FileType("json", "fa5s.code", (".json",), "json"),
    FileType("yaml", "fa5s.code", (".yml", ".yaml"), "yaml"),
    FileType("toml", "fa5s.code", (".toml",), "toml"),
    FileType("markdown", "fa5s.book", (".md", ".markdown"), "markdown"),
    FileType("shell", "fa5s.terminal", (".sh", ".bash", ".zsh", ".fish"), "shell"),
    FileType("sql", "fa5s.database", (".sql",), "sql"),
    FileType("html", "fa5s.code", (".html", ".htm", ".xml", ".svg"), "html"),
    FileType("css", "fa5s.palette", (".css", ".scss", ".sass"), "css"),
    FileType("image", "fa5s.image", (".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".bmp", ".ico"), None),
    FileType("archive", "fa5s.file-archive", (".zip", ".tar", ".gz", ".bz2", ".xz", ".7z", ".rar", ".tgz", ".tbz", ".tbz2", ".txz", ".tzst", ".zst", ".lz4", ".lzma", ".cab", ".iso", ".jar", ".war", ".ear", ".apk"), None),
    FileType("log", "fa5s.file-alt", (".log",), "log"),
    FileType("text", "fa5s.file-alt", (".txt", ".conf", ".ini", ".env"), "text"),
    FileType("docker", "fa5s.box", ("dockerfile", "compose.yml", "compose.yaml", "docker-compose.yml", "docker-compose.yaml"), "yaml"),
)

DARK_COLORS = {
    "folder": "#74a7ff", "python": "#5cff8a", "javascript": "#f7df1e",
    "json": "#d68cff", "yaml": "#d68cff", "toml": "#d68cff",
    "markdown": "#7fb4ff", "shell": "#52e8ff", "sql": "#72d0ff",
    "html": "#ff8c69", "css": "#6fa8ff", "image": "#ffd866",
    "archive": "#c59cff", "log": "#ffb86b", "text": "#9aa6b5",
    "docker": "#6cc4ff", "file": "#9fb0c3", "link": "#b7c7ff",
}
LIGHT_COLORS = {
    "folder": "#2563eb", "python": "#15803d", "javascript": "#a16207",
    "json": "#7e22ce", "yaml": "#7e22ce", "toml": "#7e22ce",
    "markdown": "#2563eb", "shell": "#0f766e", "sql": "#0369a1",
    "html": "#c2410c", "css": "#1d4ed8", "image": "#a16207",
    "archive": "#7e22ce", "log": "#c2410c", "text": "#64748b",
    "docker": "#0369a1", "file": "#475569", "link": "#4338ca",
}


directory_icon = "fa5s.folder"


def classify(name: str, *, is_dir: bool = False, is_symlink: bool = False) -> FileType:
    if is_dir:
        return FileType("link" if is_symlink else "folder", "fa5s.folder-open" if is_symlink else directory_icon)
    lower = (name or "").lower()
    base = lower.rsplit("/", 1)[-1]
    for item in FILES:
        if base == item.key or base in item.extensions or any(lower.endswith(ext) for ext in item.extensions):
            return item
    return FileType("link" if is_symlink else "file", "fa5s.link" if is_symlink else "fa5s.file")


def color_for(key: str, theme: str = "dark") -> str:
    palette = LIGHT_COLORS if str(theme).lower() == "light" else DARK_COLORS
    return palette.get(key, palette["file"])
