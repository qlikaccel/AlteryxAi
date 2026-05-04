# Start both backend and frontend servers

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "🚀 QLIK SENSE MIGRATION TOOL STARTUP" -ForegroundColor Cyan
Write-Host "========================================`n" -ForegroundColor Cyan

# Start Backend
Write-Host "📦 Starting FastAPI Backend..." -ForegroundColor Yellow
$backendPath = "D:\samFullCodeQlik\QlikSense\qlik_app\qlik\qlik-fastapi-backend"
$frontendPath = "D:\samFullCodeQlik\QlikSense\qlik_app\converter\csv"

Write-Host "`n🔧 Backend: http://127.0.0.1:8000" -ForegroundColor Green
Write-Host "   API Docs: http://127.0.0.1:8000/docs" -ForegroundColor Green
Write-Host "   Starting in new terminal...`n" -ForegroundColor Cyan

# Open backend in new PowerShell terminal
$backendScript = {
    cd $args[0]
    Write-Host "Backend starting..." -ForegroundColor Yellow
    python -m uvicorn main:app --reload --host 127.0.0.1 --port 8000
}
Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd '$backendPath'; python -m uvicorn main:app --reload --host 127.0.0.1 --port 8000"

# Start Frontend
Write-Host "⚡ Starting Vite Frontend..." -ForegroundColor Yellow
Write-Host "   Frontend: http://127.0.0.1:5173" -ForegroundColor Green
Write-Host "   Starting in new terminal...`n" -ForegroundColor Cyan

# Open frontend in new PowerShell terminal
Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd '$frontendPath'; npm run dev"

Write-Host "`n✅ Both servers are starting!`n" -ForegroundColor Green
Write-Host "📌 Wait 10-15 seconds for servers to fully initialize" -ForegroundColor Yellow
Write-Host "📌 Check the backend & frontend terminals for any errors" -ForegroundColor Yellow
Write-Host "📌 Then open http://127.0.0.1:5173 in your browser`n" -ForegroundColor Yellow

Start-Sleep -Seconds 2
Write-Host "⏳ This window will close in 5 seconds..." -ForegroundColor Cyan
Start-Sleep -Seconds 5
