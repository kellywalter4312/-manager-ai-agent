"""A real, minimal, JS-capable browser backend - genuine Chromium
rendering and JavaScript execution, driven over the Chrome DevTools
Protocol, using no dependency beyond what this project's environment
already has (the OS's own installed Chromium-based browser, and the
``websockets`` package). See ``cdp_browser.py``'s own docstring for the
full "why this, not Playwright/Selenium" reasoning.
"""
