// Manual connectivity diagnostics only. No Discord credentials or scheduled jobs.
// Run probeRobots first and review policy before running probeLists.
function probeRobots() {
  probeUrls_(['https://www.ppomppu.co.kr/robots.txt', 'https://quasarzone.com/robots.txt'], true);
}

function probeLists() {
  probeUrls_(['https://www.ppomppu.co.kr/zboard/zboard.php?id=ppomppu', 'https://quasarzone.com/bbs/qb_saleinfo'], false);
}

function probeUrls_(urls, policy) {
  urls.forEach(function(url) {
    try {
      const response = UrlFetchApp.fetch(url, {
        headers: {'User-Agent': 'HotdealNotifier/1.0 (personal deal feed reader)', 'Accept-Encoding': 'gzip'},
        followRedirects: false,
        muteHttpExceptions: true,
        validateHttpsCertificates: true
      });
      const headers = response.getAllHeaders();
      const contentType = headers['Content-Type'] || headers['content-type'] || '';
      const charset = /charset\s*=\s*([^;\s]+)/i.exec(contentType);
      const body = response.getContentText(charset ? charset[1].replace(/["']/g, '') : 'UTF-8');
      const title = /<title[^>]*>([\s\S]*?)<\/title>/i.exec(body);
      console.log(JSON.stringify({url: url, status: response.getResponseCode(),
        location: headers.Location || headers.location || null,
        bytes: response.getContent().length,
        title: title ? title[1] : '',
        rows: (body.match(/baseList-title|class="subject-link"/g) || []).length,
        policy: policy ? body.slice(0, 6000) : undefined}));
    } catch (error) {
      console.log(JSON.stringify({url: url, error: String(error)}));
    }
  });
}
