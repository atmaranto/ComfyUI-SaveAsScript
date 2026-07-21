"""Drives a running ComfyUI through the browser to export a workflow as a script.

Opens a workflow, clicks the "Save as Script" button, and writes the downloaded Python
file to --output. Used by the daily CI run, but it works against any local ComfyUI:

    python e2e_save_as_script.py --workflow test_workflow.json --output out.py
"""

import argparse
import os
import re
import sys

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

# The frontend takes a while to boot on a cold CPU-only container
READY_TIMEOUT = 180_000
ACTION_TIMEOUT = 60_000
# Generating the script imports every node module, so give it room
DOWNLOAD_TIMEOUT = 300_000


def dismiss_startup_dialogs(page):
    """Closes the welcome/template dialogs some frontend versions open on first load."""
    for _ in range(3):
        closed = False
        for selector in ('[data-pc-name="dialog"] [aria-label="Close"]',
                         '[data-pc-name="dialog"] .p-dialog-close-button',
                         '.p-dialog-header-close'):
            button = page.locator(selector)
            if button.count() and button.first.is_visible():
                button.first.click()
                closed = True
                page.wait_for_timeout(500)
        if not closed:
            break
    page.keyboard.press("Escape")
    page.wait_for_timeout(500)


def first_visible(locator):
    """Returns the first visible match, or None."""
    for i in range(locator.count()):
        if locator.nth(i).is_visible():
            return locator.nth(i)
    return None


def click_menu_item(page, label):
    """Clicks a menu entry by its visible label."""
    exact = re.compile(rf"^\s*{re.escape(label)}\s*$", re.IGNORECASE)
    for candidate in (page.get_by_role("menuitem", name=exact),
                      page.locator(f'xpath=//*[normalize-space(text())="{label}"]')):
        item = first_visible(candidate)
        if item:
            item.click()
            return True
    return False


def open_workflow(page, workflow):
    """Loads `workflow` the way a user opens a workflow file.

    Current ComfyUI has no File > Open menu; the open-workflow command
    (Comfy.OpenWorkflow) is bound to Ctrl+O and clicks a hidden file input. Older
    frontends exposed the same command under File > Open, so that is tried second.
    """
    try:
        with page.expect_file_chooser(timeout=20_000) as chooser_info:
            page.keyboard.press("Control+o")
        chooser_info.value.set_files(workflow)
        return
    except PlaywrightTimeoutError:
        print("Ctrl+O did not open a file chooser; trying the File > Open menu",
              file=sys.stderr)

    with page.expect_file_chooser(timeout=ACTION_TIMEOUT) as chooser_info:
        if not click_menu_item(page, "File"):
            raise RuntimeError(
                "Could not open a workflow: Ctrl+O opened no file chooser and there is "
                "no 'File' menu. ComfyUI's open-workflow entry point may have changed."
            )
        page.wait_for_timeout(500)
        if not click_menu_item(page, "Open"):
            raise RuntimeError("Found a 'File' menu but no 'Open' item inside it.")
    chooser_info.value.set_files(workflow)


def wait_for_workflow(page, expect_node):
    """Waits until the loaded graph actually contains `expect_node`."""
    if not expect_node:
        page.wait_for_timeout(2000)
        return
    try:
        page.wait_for_function(
            """(type) => (window.app?.graph?._nodes ?? []).some(
                   (n) => n?.type === type || n?.comfyClass === type)""",
            arg=expect_node,
            timeout=ACTION_TIMEOUT,
        )
    except PlaywrightTimeoutError:
        # Not every frontend exposes window.app; the exported script is checked
        # separately, so this is a warning rather than a failure.
        print(f"warning: could not confirm a {expect_node} node via window.app",
              file=sys.stderr)
    page.wait_for_timeout(1000)


def find_save_as_script_button(page):
    """Returns the visible "Save as Script" button.

    Matching is on button text, not accessible name: the menubar button carries an
    aria-label ("Save the current workflow as a Python script") that would otherwise
    hide it from a name-based lookup. The extension also adds a button to the legacy
    .comfy-menu sidebar, which is normally hidden.
    """
    label = re.compile("Save as Script", re.IGNORECASE)
    for candidate in (page.locator("button").filter(has_text=label),
                      page.get_by_role("button", name=label),
                      page.locator('button[aria-label*="Python script" i]')):
        button = first_visible(candidate)
        if button:
            return button

    raise RuntimeError(
        "The 'Save as Script' button is not present. The extension's setup() may have "
        "failed to run - check the browser console output above."
    )


def export_script(page, name):
    """Clicks "Save as Script", answers the filename prompt, returns the download."""
    button = find_save_as_script_button(page)

    # The extension asks for the filename with window.prompt(); without a handler
    # Playwright auto-dismisses it and no download is ever produced.
    page.once("dialog", lambda dialog: dialog.accept(name))

    with page.expect_download(timeout=DOWNLOAD_TIMEOUT) as download_info:
        button.click()
    return download_info.value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8188", help="Where ComfyUI is serving")
    parser.add_argument("--workflow", required=True, help="Workflow JSON to open")
    parser.add_argument("--output", required=True, help="Where to write the exported script")
    parser.add_argument("--expect-node", default=None,
                        help="Node type that must appear in the graph once loaded")
    parser.add_argument("--artifacts", default=None, help="Directory for failure screenshots")
    parser.add_argument("--headed", action="store_true", help="Show the browser (for debugging)")
    args = parser.parse_args()

    workflow = os.path.abspath(args.workflow)
    output = os.path.abspath(args.output)
    os.makedirs(os.path.dirname(output) or ".", exist_ok=True)
    if args.artifacts:
        os.makedirs(args.artifacts, exist_ok=True)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=not args.headed,
            # Chromium crashes with the small /dev/shm containers get by default
            args=["--disable-dev-shm-usage", "--no-sandbox"],
        )
        context = browser.new_context(accept_downloads=True)
        page = context.new_page()
        page.set_default_timeout(ACTION_TIMEOUT)
        page.on("console", lambda msg: print(f"[browser:{msg.type}] {msg.text}"))
        page.on("pageerror", lambda err: print(f"[browser:pageerror] {err}", file=sys.stderr))

        try:
            page.goto(args.url, wait_until="domcontentloaded", timeout=READY_TIMEOUT)
            page.wait_for_selector("#graph-canvas", timeout=READY_TIMEOUT)
            page.wait_for_timeout(3000)
            dismiss_startup_dialogs(page)

            open_workflow(page, workflow)
            wait_for_workflow(page, args.expect_node)

            download = export_script(page, os.path.splitext(os.path.basename(output))[0])
            download.save_as(output)
            print(f"Exported script to {output}")
        except Exception:
            if args.artifacts:
                page.screenshot(path=os.path.join(args.artifacts, "failure.png"), full_page=True)
                with open(os.path.join(args.artifacts, "failure.html"), "w", encoding="utf-8") as f:
                    f.write(page.content())
                print(f"Wrote failure artifacts to {args.artifacts}", file=sys.stderr)
            raise
        finally:
            context.close()
            browser.close()

    if not os.path.getsize(output):
        raise SystemExit("The downloaded script is empty")


if __name__ == "__main__":
    main()
