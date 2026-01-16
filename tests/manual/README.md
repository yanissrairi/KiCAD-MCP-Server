# Manual Integration Tests

These are manual verification scripts that require human interaction or specific external setup that cannot be easily automated in CI.

## IPC Backend Tests (`test_ipc_backend.py`)

### Purpose

Manual smoke testing for KiCAD IPC real-time integration:
- **Pre-release validation**: Run before releasing new versions to ensure IPC works correctly
- **Debugging tool**: Reproduce and debug IPC-related issues reported by users
- **Visual verification**: Confirm that changes appear immediately in KiCAD UI
- **Living documentation**: Demonstrate how to use the IPC backend API

### Prerequisites

1. **KiCAD 9.0+** must be running
2. **IPC API enabled**: `Preferences > Plugins > Enable IPC API Server`
3. **PCB board open** in the PCB editor (any board will work)

### Usage

```bash
# Run all verification tests (non-interactive)
python tests/manual/test_ipc_backend.py

# Run in interactive mode (prompts before each modification)
python tests/manual/test_ipc_backend.py --interactive
```

### What it Verifies

1. **IPC Connection**: Tests connection to running KiCAD instance
2. **Board Access**: Validates ability to access the open board
3. **Real-time Track Addition**: Adds a test track and verifies it appears in UI
4. **Real-time Via Addition**: Adds a test via and verifies it appears in UI
5. **Real-time Text Addition**: Adds test text and verifies it appears in UI
6. **Selection Detection**: Tests reading the current selection from KiCAD UI

### Expected Behavior

Each test should:
- ✅ Execute without errors
- ✅ Show immediate changes in KiCAD UI (no need to refresh)
- ✅ Log success messages in the console

If running in interactive mode, you'll be prompted before each modification to confirm you want to proceed.

## Why Manual?

These tests validate the **actual user experience** with KiCAD's UI, which is challenging to automate without:
- Complex headless setup (Xvfb on Linux)
- Platform-specific UI automation (different on Windows/macOS/Linux)
- Significant CI resources (launching KiCAD for each test run)

**Current priorities:**
1. 🎯 **Unit tests** for business logic (high ROI, fast, reliable)
2. 🎯 **Integration tests** with mocked I/O (medium ROI, fast)
3. ⚡ **Manual tests** for smoke testing (low overhead, human validation)
4. 🔮 **Automated UI tests** (future, if ROI justifies complexity)

## When to Run

Run these manual tests when:
- 🚀 Before releasing a new version
- 🐛 Debugging IPC-related bug reports
- 🔧 After making changes to `ipc_backend.py`
- 📖 Learning how the IPC API works

## Future Automation

These tests could be automated with:
- **Xvfb** (Linux): Virtual framebuffer for headless UI
- **Docker**: Containerized KiCAD + Xvfb environment
- **GitHub Actions**: Self-hosted runner with KiCAD installed

However, the **ROI is currently low** compared to writing unit tests for the business logic (which would increase coverage from 0.45% → 80%).

## Contributing

If you add new IPC features, consider adding verification tests here to make manual testing easier for maintainers and contributors.
