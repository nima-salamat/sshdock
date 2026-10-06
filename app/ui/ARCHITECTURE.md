# UI architecture

```
app/
  docker/
    manager.py      # CLI wrapper
    models.py       # typed Container/Image/Volume/Network/Event
    errors.py       # classify_docker_result
  services/
    containers.py   # UI-agnostic container operations
  ui/
    app.py
    main_window.py  # shell + orchestration (still large; pages gradually extracted)
    compose_panel.py
    container_details.py
    dialogs.py
    files via app/files/browser.py
```

## Growth direction

1. Move page builders from `main_window` into `ui/pages/*`
2. Route all Docker mutations through `services.*` + `classify_docker_result`
3. Prefer typed models (`ContainerInfo`) at UI boundaries
