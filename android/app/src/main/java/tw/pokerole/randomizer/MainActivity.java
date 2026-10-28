package tw.pokerole.randomizer;

import android.app.Activity;
import android.app.DownloadManager;
import android.content.Context;
import android.content.res.Configuration;
import android.net.Uri;
import android.os.Bundle;
import android.os.Environment;
import android.view.ViewGroup;
import android.webkit.DownloadListener;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Toast;

public class MainActivity extends Activity {
    private WebView webView;
    private boolean localFallbackLoaded = false;
    private boolean reloadAfterResume = false;
    private boolean remotePageStarted = false;
    private static final String LOCAL_URL = "file:///android_asset/index.html";

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        webView = new WebView(this);
        WebSettings settings = webView.getSettings();
        settings.setJavaScriptEnabled(true);
        settings.setDomStorageEnabled(true);
        settings.setUseWideViewPort(true);
        settings.setLoadWithOverviewMode(false);
        settings.setSupportZoom(true);
        settings.setBuiltInZoomControls(false);
        // 不使用舊的 WebView 快取，讓重新開啟 App 時取得 GitHub Pages 最新版本。
        settings.setCacheMode(WebSettings.LOAD_NO_CACHE);
        webView.setWebViewClient(new WebViewClient() {
            @Override
            public void onPageStarted(WebView view, String url, android.graphics.Bitmap favicon) {
                super.onPageStarted(view, url, favicon);
                if (url != null && url.startsWith("https://")) remotePageStarted = true;
            }

            @Override
            public void onReceivedError(WebView view, WebResourceRequest request, WebResourceError error) {
                super.onReceivedError(view, request, error);
                if (request != null && request.isForMainFrame() && !localFallbackLoaded && request.getUrl() != null
                        && !"file".equalsIgnoreCase(request.getUrl().getScheme())) {
                    loadLocalFallback();
                }
            }
        });
        webView.setWebChromeClient(new WebChromeClient());
        webView.setDownloadListener((url, userAgent, contentDisposition, mimeType, contentLength) -> {
            try {
                DownloadManager.Request request = new DownloadManager.Request(Uri.parse(url));
                request.setTitle("PokeRole3.apk");
                request.setDescription("正在下載 PokeRole Android 安裝檔");
                request.setMimeType("application/vnd.android.package-archive");
                if (userAgent != null) request.addRequestHeader("User-Agent", userAgent);
                request.setNotificationVisibility(DownloadManager.Request.VISIBILITY_VISIBLE_NOTIFY_COMPLETED);
                request.setDestinationInExternalPublicDir(Environment.DIRECTORY_DOWNLOADS, "PokeRole3.apk");
                DownloadManager manager = (DownloadManager) getSystemService(Context.DOWNLOAD_SERVICE);
                manager.enqueue(request);
                Toast.makeText(this, "已開始下載 APK，完成後請從通知或下載資料夾開啟。", Toast.LENGTH_LONG).show();
            } catch (Exception ex) {
                Toast.makeText(this, "APK 下載失敗，請改用瀏覽器開啟下載連結。", Toast.LENGTH_LONG).show();
            }
        });
        webView.setLayoutParams(new ViewGroup.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));
        setContentView(webView);

        String targetUrl = BuildConfig.WEB_APP_URL;
        if (targetUrl.contains("YOUR_GITHUB_USERNAME") || targetUrl.contains("YOUR_REPOSITORY") || !targetUrl.startsWith("https://")) {
            loadLocalFallback();
        } else {
            webView.loadUrl(targetUrl);
        }
    }

    private void loadLocalFallback() {
        if (webView == null || localFallbackLoaded) return;
        localFallbackLoaded = true;
        Toast.makeText(this, "目前無法連線至線上網頁，已切換到 APK 內建版本。重新開啟 App 可再次檢查線上更新。", Toast.LENGTH_LONG).show();
        webView.loadUrl(LOCAL_URL);
    }

    @Override
    protected void onPause() {
        super.onPause();
        if (webView != null && remotePageStarted && !localFallbackLoaded) reloadAfterResume = true;
    }

    @Override
    protected void onResume() {
        super.onResume();
        if (webView != null && reloadAfterResume && !localFallbackLoaded) {
            reloadAfterResume = false;
            webView.reload();
        }
    }

    @Override
    public void onConfigurationChanged(Configuration newConfig) {
        super.onConfigurationChanged(newConfig);
        if (webView != null) {
            webView.requestLayout();
            webView.invalidate();
        }
    }

    @Override
    public void onBackPressed() {
        if (webView != null && webView.canGoBack()) webView.goBack();
        else super.onBackPressed();
    }
}
