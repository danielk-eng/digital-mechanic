package ru.iikuznitsa.mechanic;

import android.Manifest;
import android.app.AlertDialog;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.graphics.Color;
import android.net.Uri;
import android.os.Bundle;
import android.view.KeyEvent;
import android.view.ViewGroup;
import android.webkit.CookieManager;
import android.webkit.JavascriptInterface;
import android.webkit.PermissionRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.widget.EditText;
import android.widget.FrameLayout;
import androidx.core.content.ContextCompat;
import androidx.core.graphics.Insets;
import androidx.core.view.ViewCompat;
import androidx.core.view.WindowInsetsCompat;
import com.getcapacitor.BridgeActivity;
import com.getcapacitor.BridgeWebChromeClient;
import com.getcapacitor.BridgeWebViewClient;

/**
 * Loads the contest stand in a WebView. The server URL is configurable
 * (default: the Vercel stand) and can be changed from the launcher shortcut,
 * a long-press of Back, an intent extra, or the offline page.
 */
public class MainActivity extends BridgeActivity {
  public static final String PREFS = "mechanic";
  public static final String KEY_URL = "server_url";
  public static final String DEFAULT_URL = "https://digital-mechanic-iikuznitsa.vercel.app/m";
  public static final String ACTION_EDIT_SERVER = "ru.iikuznitsa.mechanic.EDIT_SERVER";
  private static final int REQ_MEDIA = 42;
  private static final String LOCAL = "file:///android_asset/public/index.html";
  private static final String OFFLINE = LOCAL + "?offline=1";

  private PermissionRequest pendingRequest;

  @Override
  public void onCreate(Bundle savedInstanceState) {
    super.onCreate(savedInstanceState);
    WebView webView = getBridge().getWebView();
    if (webView.getParent() instanceof android.view.View) {
      ((android.view.View) webView.getParent()).setBackgroundColor(Color.rgb(14, 16, 19));
    }
    applySystemBarInsets(webView);
    CookieManager cookies = CookieManager.getInstance();
    cookies.setAcceptCookie(true);
    cookies.setAcceptThirdPartyCookies(webView, true);
    WebSettings settings = webView.getSettings();
    settings.setJavaScriptEnabled(true);
    settings.setDomStorageEnabled(true);
    settings.setMediaPlaybackRequiresUserGesture(false);
    settings.setMixedContentMode(WebSettings.MIXED_CONTENT_COMPATIBILITY_MODE);
    webView.addJavascriptInterface(new NativeBridge(), "MechanicNative");
    webView.setWebViewClient(new BridgeWebViewClient(getBridge()) {
      @Override
      public void onReceivedError(WebView view, android.webkit.WebResourceRequest request,
                                  android.webkit.WebResourceError error) {
        if (request != null && request.isForMainFrame()) {
          view.loadUrl(offlineUrl());
        } else {
          super.onReceivedError(view, request, error);
        }
      }

      @Override
      @SuppressWarnings("deprecation")
      public void onReceivedError(WebView view, int errorCode, String description, String failingUrl) {
        if (failingUrl != null && !failingUrl.startsWith("file:")) {
          view.loadUrl(offlineUrl());
        } else {
          super.onReceivedError(view, errorCode, description, failingUrl);
        }
      }
    });
    webView.setWebChromeClient(new BridgeWebChromeClient(getBridge()) {
      @Override
      public void onPermissionRequest(final PermissionRequest request) {
        runOnUiThread(() -> handleWebPermission(request));
      }
    });
    handleIntent(getIntent());
  }

  @Override
  protected void onNewIntent(Intent intent) {
    super.onNewIntent(intent);
    setIntent(intent);
    handleIntent(intent);
  }

  @Override
  public boolean onKeyLongPress(int keyCode, KeyEvent event) {
    if (keyCode == KeyEvent.KEYCODE_BACK || keyCode == KeyEvent.KEYCODE_MENU) {
      showServerDialog();
      return true;
    }
    return super.onKeyLongPress(keyCode, event);
  }

  @Override
  public void onRequestPermissionsResult(int requestCode, String[] permissions, int[] grantResults) {
    super.onRequestPermissionsResult(requestCode, permissions, grantResults);
    if (requestCode == REQ_MEDIA && pendingRequest != null) {
      grantIfAllowed(pendingRequest);
    }
  }

  private void handleIntent(Intent intent) {
    if (intent == null) return;
    String extra = intent.getStringExtra("server_url");
    if (extra != null && !extra.trim().isEmpty()) {
      saveUrl(normalize(extra));
    }
    if (ACTION_EDIT_SERVER.equals(intent.getAction())) {
      getBridge().getWebView().post(this::showServerDialog);
      return;
    }
    getBridge().getWebView().post(this::loadConfiguredUrl);
  }

  private void loadConfiguredUrl() {
    // The bundled page performs the reachability probe inside WebView itself,
    // so DNS/proxy behaviour matches the real navigation.
    getBridge().getWebView().loadUrl(LOCAL);
  }

  private static void applySystemBarInsets(WebView webView) {
    ViewCompat.setOnApplyWindowInsetsListener(webView, (v, windowInsets) -> {
      Insets bars = windowInsets.getInsets(
          WindowInsetsCompat.Type.systemBars() | WindowInsetsCompat.Type.displayCutout());
      ViewGroup.LayoutParams lp = v.getLayoutParams();
      if (lp instanceof ViewGroup.MarginLayoutParams) {
        ViewGroup.MarginLayoutParams mlp = (ViewGroup.MarginLayoutParams) lp;
        if (mlp.leftMargin != bars.left || mlp.topMargin != bars.top
            || mlp.rightMargin != bars.right || mlp.bottomMargin != bars.bottom) {
          mlp.leftMargin = bars.left;
          mlp.topMargin = bars.top;
          mlp.rightMargin = bars.right;
          mlp.bottomMargin = bars.bottom;
          v.setLayoutParams(mlp);
        }
      }
      return windowInsets;
    });
    ViewCompat.requestApplyInsets(webView);
  }

  private String currentServerUrl() {
    return normalize(prefs().getString(KEY_URL, DEFAULT_URL));
  }

  private void saveUrl(String url) {
    prefs().edit().putString(KEY_URL, normalize(url)).apply();
  }

  private SharedPreferences prefs() {
    return getSharedPreferences(PREFS, MODE_PRIVATE);
  }

  private String offlineUrl() {
    return OFFLINE + "&url=" + Uri.encode(currentServerUrl());
  }

  static String normalize(String raw) {
    if (raw == null) return DEFAULT_URL;
    String u = raw.trim();
    if (u.isEmpty()) return DEFAULT_URL;
    if (!u.startsWith("http://") && !u.startsWith("https://")) {
      u = "https://" + u;
    }
    Uri parsed = Uri.parse(u);
    String scheme = parsed.getScheme();
    if (!"http".equals(scheme) && !"https".equals(scheme)) return DEFAULT_URL;
    String host = parsed.getHost();
    if (host == null || host.isEmpty()) return DEFAULT_URL;
    String path = parsed.getPath();
    if (path != null && path.endsWith("/") && path.length() > 1) {
      path = path.substring(0, path.length() - 1);
    }
    Uri.Builder b = new Uri.Builder().scheme(scheme).encodedAuthority(parsed.getEncodedAuthority());
    if (path != null && !path.isEmpty() && !"/".equals(path)) b.path(path);
    if (parsed.getEncodedQuery() != null) b.encodedQuery(parsed.getEncodedQuery());
    return b.build().toString();
  }

  void showServerDialog() {
    final EditText input = new EditText(this);
    input.setText(currentServerUrl());
    input.setHint(DEFAULT_URL);
    input.setSingleLine(true);
    input.setInputType(android.text.InputType.TYPE_TEXT_VARIATION_URI);
    float density = getResources().getDisplayMetrics().density;
    int pad = Math.round(20 * density);
    FrameLayout wrap = new FrameLayout(this);
    wrap.setPadding(pad, pad, pad, Math.round(8 * density));
    wrap.addView(input);
    new AlertDialog.Builder(this)
        .setTitle("Адрес сервера")
        .setMessage("Стенд, к которому подключается приложение.")
        .setView(wrap)
        .setPositiveButton("Подключить", (d, w) -> {
          saveUrl(input.getText().toString());
          loadConfiguredUrl();
        })
        .setNeutralButton("По умолчанию", (d, w) -> {
          prefs().edit().remove(KEY_URL).apply();
          loadConfiguredUrl();
        })
        .setNegativeButton("Отмена", null)
        .show();
  }

  private void handleWebPermission(PermissionRequest request) {
    pendingRequest = request;
    java.util.ArrayList<String> needed = new java.util.ArrayList<>();
    for (String r : request.getResources()) {
      if (PermissionRequest.RESOURCE_AUDIO_CAPTURE.equals(r)
          && ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO)
              != PackageManager.PERMISSION_GRANTED) {
        needed.add(Manifest.permission.RECORD_AUDIO);
      }
      if (PermissionRequest.RESOURCE_VIDEO_CAPTURE.equals(r)
          && ContextCompat.checkSelfPermission(this, Manifest.permission.CAMERA)
              != PackageManager.PERMISSION_GRANTED) {
        needed.add(Manifest.permission.CAMERA);
      }
    }
    if (needed.isEmpty()) {
      grantIfAllowed(request);
    } else {
      requestPermissions(needed.toArray(new String[0]), REQ_MEDIA);
    }
  }

  private void grantIfAllowed(PermissionRequest request) {
    java.util.ArrayList<String> granted = new java.util.ArrayList<>();
    for (String r : request.getResources()) {
      if (PermissionRequest.RESOURCE_AUDIO_CAPTURE.equals(r)
          && ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO)
              == PackageManager.PERMISSION_GRANTED) {
        granted.add(r);
      } else if (PermissionRequest.RESOURCE_VIDEO_CAPTURE.equals(r)
          && ContextCompat.checkSelfPermission(this, Manifest.permission.CAMERA)
              == PackageManager.PERMISSION_GRANTED) {
        granted.add(r);
      } else if (PermissionRequest.RESOURCE_PROTECTED_MEDIA_ID.equals(r)) {
        granted.add(r);
      }
    }
    if (!granted.isEmpty()) {
      request.grant(granted.toArray(new String[0]));
    } else {
      request.deny();
    }
    pendingRequest = null;
  }

  public class NativeBridge {
    @JavascriptInterface
    public String getServerUrl() {
      return currentServerUrl();
    }

    @JavascriptInterface
    public void setServerUrl(String url) {
      saveUrl(url);
    }

    @JavascriptInterface
    public void connect(String url) {
      saveUrl(url);
      runOnUiThread(() -> getBridge().getWebView().loadUrl(currentServerUrl()));
    }

    @JavascriptInterface
    public void openSettings() {
      runOnUiThread(MainActivity.this::showServerDialog);
    }
  }
}
