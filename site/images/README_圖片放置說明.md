# 寶可夢圖片放置說明

請把圖片檔案放在本資料夾，並與 `site/data.js` 中每筆資料的 `image` 路徑及檔名一致。

例如：
- `images/001.png` → `site/images/001.png`
- `images/006-MY.png` → `site/images/006-MY.png`
- `images/999-10% Forme.png` → `site/images/999-10% Forme.png`

網頁載入圖片時會依序嘗試：
1. GitHub Pages 網站上的 `images/` 相對路徑。
2. 同一個 Repository 的 `main/site/images/` GitHub Raw 位置。
3. 同一個 Repository 根目錄的 `main/images/` GitHub Raw 位置。

如果圖片放在另一個 GitHub Repository，可以在 `site/site-config.js` 設定 `imageBaseUrl`，例如 `https://raw.githubusercontent.com/帳號/圖片Repo/main/`。此網址要指向可直接讀取圖片檔案的目錄。

原先上傳的 `data.js` 只有圖片路徑，沒有附上實際圖片檔，所以需將圖片素材上傳到上述位置後，網頁才有實際圖片可顯示。
