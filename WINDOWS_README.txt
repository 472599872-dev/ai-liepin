LiepinRecruitingAgent Windows source package

Build:
1. Install Windows 10/11 + Python 3.11 64-bit.
2. Open PowerShell in this project folder.
3. Run:
   .\scripts\build_windows.ps1 -Clean
4. The distributable zip will be generated at:
   dist\LiepinRecruitingAgent-win64.zip
   dist\LiepinRecruitingAgent-update-win64.zip

Package types:
- LiepinRecruitingAgent-win64.zip is for a clean/new installation.
- LiepinRecruitingAgent-update-win64.zip is for upgrading an existing installation.
- To update, close the app, extract the update zip into the old app folder, and overwrite existing files.
- Do not delete the old app folder before extracting the update zip.

Runtime data:
- data\app.db stores accounts, jobs, tasks, candidates, scores, logs.
- profiles\app stores Liepin login profiles/cookies.
- .env stores model API keys.
- license.json is bound to one authorized computer.

Migration:
- To migrate an old installation, close the app first.
- Prefer the update zip when staying on the same computer.
- If you must migrate manually, copy old data and profiles folders into the new app folder.
- Keep the new .env and license.json unless you intentionally want to replace them.

Notes:
- This source package intentionally does not include local app.db or browser profiles.
- Do not send packages containing .env/license.json to unrelated users.
