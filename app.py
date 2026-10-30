import sys


def main():
    if len(sys.argv) == 3 and sys.argv[1] == "--studio-apply-update":
        from vc_extractor.updater import apply_update, atomic_json, storage_root
        try:
            apply_update(sys.argv[2])
        except Exception as exc:
            atomic_json(storage_root() / "helper-error.json", {"error": str(exc)})
            raise
    else:
        from vc_extractor.webui import run
        run()

if __name__ == "__main__":
    main()
