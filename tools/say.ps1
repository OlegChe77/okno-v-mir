# Сообщения для start.bat (в самом .bat только ASCII: cmd путается в UTF-8 и ломает переходы по меткам).
param([string]$Key)
[Console]::OutputEncoding = [Text.Encoding]::UTF8
switch ($Key) {
  "download" { Write-Host ""; Write-Host "  Первый запуск: скачиваю Python (около 11 МБ, один раз)."; Write-Host "  Подождите минуту, программа запустится сама."; Write-Host "" }
  "console"  { Write-Host ""; Write-Host "  ЗАГРУЗКА... Окно в мир запускается. Браузер откроется сам."; Write-Host "  Не закрывайте это окно, пока смотрите."; Write-Host "" }
  "fail"     { Write-Host ""; Write-Host "  Не удалось скачать Python. Проверьте интернет и запустите start.bat снова."; Write-Host "  Или установите Python 3.12 с https://www.python.org/downloads/ (отметьте 'Add python.exe to PATH')."; Write-Host "" }
}
