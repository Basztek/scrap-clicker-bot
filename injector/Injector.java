import android.graphics.PixelFormat;
import android.hardware.display.DisplayManager;
import android.hardware.display.VirtualDisplay;
import android.media.Image;
import android.media.ImageReader;
import android.os.IBinder;
import android.os.SystemClock;
import android.view.InputDevice;
import android.view.InputEvent;
import android.view.MotionEvent;
import android.view.Surface;

import java.io.BufferedReader;
import java.io.IOException;
import java.io.InputStreamReader;
import java.lang.reflect.Method;
import java.nio.ByteBuffer;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Long-running touch injector and screen grabber, started on the phone with
 * app_process (like scrcpy). Much faster than `input motionevent`, which starts
 * a new process per event, and much lighter than streaming screenrecord video.
 *
 * Reads one command per line from stdin:
 *   d X Y     finger down     m X Y   move     u X Y   finger up
 *   d ID X Y [ID X Y...]  the same for several fingers (multi-touch), ID = any number per finger;
 *             "d X Y" is finger 0
 *   s MS      sleep           sync    answers "ok" once everything before it was injected
 *   frame W H answers "frame W H STRIDE" and STRIDE*H bytes of RGBA: the screen scaled to WxH
 *   screen 0  turn the display panel off (apps keep running, frames keep working)
 *   screen 1  turn it back on; also done automatically when stdin closes
 *   atexit CMD  run shell command CMD when stdin closes (however the bot on the PC ended)
 */
public final class Injector {
    private static final int INJECT_MODE_ASYNC = 0;
    private static final int POWER_MODE_OFF = 0;
    private static final int POWER_MODE_NORMAL = 2;

    private static Object inputManager;
    private static Method injectInputEvent;
    private static int deviceId = -1;
    private static long downTime;
    // Fingers on the screen, in the order they went down: id -> {x, y}
    private static final Map<Integer, float[]> pointers = new LinkedHashMap<>();
    private static boolean screenOff;
    private static IBinder[] displayTokens;
    private static ImageReader reader;
    private static VirtualDisplay mirror;
    private static byte[] lastFrame;
    private static String lastFrameHeader;
    private static final List<String> exitCommands = new ArrayList<>();

    public static void main(String[] args) throws Exception {
        Class<?> managerClass;
        try {
            managerClass = Class.forName("android.hardware.input.InputManagerGlobal");  // Android 14+
        } catch (ClassNotFoundException e) {
            managerClass = Class.forName("android.hardware.input.InputManager");
        }
        inputManager = managerClass.getMethod("getInstance").invoke(null);
        injectInputEvent = managerClass.getMethod("injectInputEvent", InputEvent.class, int.class);

        // Report events as coming from the real touchscreen instead of a virtual device (-1)
        try {
            for (int id : InputDevice.getDeviceIds()) {
                InputDevice device = InputDevice.getDevice(id);
                if (device != null && device.supportsSource(InputDevice.SOURCE_TOUCHSCREEN)) {
                    deviceId = id;
                    break;
                }
            }
        } catch (RuntimeException e) {
            deviceId = -1;
        }

        System.out.println("ready " + deviceId);
        System.out.flush();

        try {
            handleCommands(new BufferedReader(new InputStreamReader(System.in)));
        } catch (IOException e) {
            // Connection to the PC lost: clean up just like at a normal end of input
        }
        // A finger left down (the bot killed in the middle of a drag) makes the game
        // ignore every later touch until it is paused and resumed
        if (!pointers.isEmpty()) {
            inject(MotionEvent.ACTION_CANCEL, -1);
            pointers.clear();
        }
        if (screenOff) {
            setScreenPower(true);
        }
        for (String command : exitCommands) {
            try {
                Runtime.getRuntime().exec(new String[]{"sh", "-c", command}).waitFor();
            } catch (Exception e) {
                System.err.println("could not run " + command + ": " + e);
            }
        }
        // Skip the normal VM shutdown, which crashes in the native display library
        Runtime.getRuntime().halt(0);
    }

    private static void handleCommands(BufferedReader input) throws Exception {
        String line;
        while ((line = input.readLine()) != null) {
            String[] parts = line.trim().split(" ");
            switch (parts[0]) {
                case "d":
                case "m":
                case "u":
                    touch(parts);
                    break;
                case "s":
                    SystemClock.sleep(Long.parseLong(parts[1]));
                    break;
                case "sync":
                    System.out.println("ok");
                    System.out.flush();
                    break;
                case "screen":
                    setScreenPower(parts[1].equals("1"));
                    break;
                case "frame":
                    sendFrame(Integer.parseInt(parts[1]), Integer.parseInt(parts[2]));
                    break;
                case "atexit":
                    exitCommands.add(line.trim().substring("atexit ".length()));
                    break;
                default:
                    break;
            }
        }
    }

    /**
     * Writes a fresh frame of a small virtual display that mirrors the screen.
     * The system only renders into it; nothing gets video-encoded. The mirror
     * stays attached: detaching it between frames re-adds the display every
     * time, which every app (the game included) gets notified about.
     */
    private static void sendFrame(int width, int height) throws Exception {
        if (mirror == null) {
            reader = ImageReader.newInstance(width, height, PixelFormat.RGBA_8888, 3);
            Method createVirtualDisplay = DisplayManager.class.getMethod("createVirtualDisplay",
                    String.class, int.class, int.class, int.class, Surface.class);
            mirror = (VirtualDisplay) createVirtualDisplay.invoke(null, "merger", width, height, 0,
                    reader.getSurface());
        }

        // Queued images can be old, so wait for one rendered after this request.
        // A new image only arrives when the screen changes; if none does, the
        // newest queued one (or the last sent one) is still current.
        Image image = reader.acquireLatestImage();
        long deadline = SystemClock.uptimeMillis() + (lastFrame == null && image == null ? 2000 : 100);
        while (SystemClock.uptimeMillis() < deadline) {
            SystemClock.sleep(4);
            Image fresh = reader.acquireLatestImage();
            if (fresh != null) {
                if (image != null) {
                    image.close();
                }
                image = fresh;
                break;
            }
        }
        if (image != null) {
            Image.Plane plane = image.getPlanes()[0];
            ByteBuffer buffer = plane.getBuffer();
            int stride = plane.getRowStride();
            int size = stride * (image.getHeight() - 1) + image.getWidth() * plane.getPixelStride();
            if (lastFrame == null || lastFrame.length != stride * image.getHeight()) {
                lastFrame = new byte[stride * image.getHeight()];
            }
            buffer.get(lastFrame, 0, size);  // the last row may be shorter than the stride
            lastFrameHeader = "frame " + image.getWidth() + " " + image.getHeight() + " " + stride + "\n";
            image.close();
        }
        if (lastFrame == null) {
            System.out.print("frame 0 0 0\n");
        } else {
            System.out.print(lastFrameHeader);
            System.out.write(lastFrame, 0, lastFrame.length);
        }
        System.out.flush();
    }

    /** Switches the physical display panel on/off without putting the phone to sleep (as scrcpy does). */
    private static void setScreenPower(boolean on) {
        try {
            Class<?> surfaceControl = Class.forName("android.view.SurfaceControl");
            Method setPowerMode = surfaceControl.getMethod("setDisplayPowerMode", IBinder.class, int.class);
            // Looked up only once: the native library behind it can't be loaded twice
            if (displayTokens == null) {
                displayTokens = physicalDisplayTokens(surfaceControl);
            }
            for (IBinder token : displayTokens) {
                setPowerMode.invoke(null, token, on ? POWER_MODE_NORMAL : POWER_MODE_OFF);
            }
            screenOff = !on;
        } catch (Exception e) {
            System.err.println("could not switch the screen " + (on ? "on" : "off") + ": "
                    + (e.getCause() != null ? e.getCause() : e));
        }
    }

    private static IBinder[] physicalDisplayTokens(Class<?> surfaceControl) throws Exception {
        try {
            // Android 14+: the display ids/tokens moved to DisplayControl in services.jar
            Class<?> loaderFactory = Class.forName("com.android.internal.os.ClassLoaderFactory");
            Method createClassLoader = loaderFactory.getDeclaredMethod("createClassLoader", String.class,
                    String.class, String.class, ClassLoader.class, int.class, boolean.class, String.class);
            ClassLoader loader = (ClassLoader) createClassLoader.invoke(null, "/system/framework/services.jar",
                    null, null, ClassLoader.getSystemClassLoader(), 0, true, null);
            Class<?> displayControl = loader.loadClass("com.android.server.display.DisplayControl");
            Method loadLibrary = Runtime.class.getDeclaredMethod("loadLibrary0", Class.class, String.class);
            loadLibrary.setAccessible(true);
            loadLibrary.invoke(Runtime.getRuntime(), displayControl, "android_servers");

            long[] ids = (long[]) displayControl.getMethod("getPhysicalDisplayIds").invoke(null);
            Method getToken = displayControl.getMethod("getPhysicalDisplayToken", long.class);
            IBinder[] tokens = new IBinder[ids.length];
            for (int i = 0; i < ids.length; i++) {
                tokens[i] = (IBinder) getToken.invoke(null, ids[i]);
            }
            return tokens;
        } catch (ClassNotFoundException | NoSuchMethodException e) {
            // Older Android: SurfaceControl has them
            long[] ids = (long[]) surfaceControl.getMethod("getPhysicalDisplayIds").invoke(null);
            Method getToken = surfaceControl.getMethod("getPhysicalDisplayToken", long.class);
            IBinder[] tokens = new IBinder[ids.length];
            for (int i = 0; i < ids.length; i++) {
                tokens[i] = (IBinder) getToken.invoke(null, ids[i]);
            }
            return tokens;
        }
    }

    /** "d/m/u X Y" (finger 0) or "d/m/u ID X Y ID X Y...": every finger listed goes down / moves / goes up. */
    private static void touch(String[] parts) throws Exception {
        boolean single = parts.length == 3;
        for (int i = 1; i + (single ? 1 : 2) < parts.length; i += single ? 2 : 3) {
            int id = single ? 0 : Integer.parseInt(parts[i]);
            int at = single ? i : i + 1;
            float[] position = {Float.parseFloat(parts[at]), Float.parseFloat(parts[at + 1])};
            switch (parts[0]) {
                case "d":
                    if (pointers.isEmpty()) {
                        downTime = SystemClock.uptimeMillis();
                    }
                    pointers.remove(id);
                    pointers.put(id, position);
                    inject(pointers.size() == 1 ? MotionEvent.ACTION_DOWN : MotionEvent.ACTION_POINTER_DOWN, id);
                    break;
                case "u":
                    if (!pointers.containsKey(id)) {
                        break;
                    }
                    pointers.put(id, position);
                    inject(pointers.size() == 1 ? MotionEvent.ACTION_UP : MotionEvent.ACTION_POINTER_UP, id);
                    pointers.remove(id);
                    break;
                default:
                    if (pointers.containsKey(id)) {
                        pointers.put(id, position);
                    }
                    break;
            }
        }
        // One move event carries all fingers
        if (parts[0].equals("m") && !pointers.isEmpty()) {
            inject(MotionEvent.ACTION_MOVE, -1);
        }
    }

    /** Injects an event with every finger on the screen; changed = the finger going down/up. */
    private static void inject(int action, int changed) throws Exception {
        int count = pointers.size();
        MotionEvent.PointerProperties[] properties = new MotionEvent.PointerProperties[count];
        MotionEvent.PointerCoords[] coords = new MotionEvent.PointerCoords[count];
        int index = 0;
        for (Map.Entry<Integer, float[]> pointer : pointers.entrySet()) {
            properties[index] = new MotionEvent.PointerProperties();
            properties[index].id = pointer.getKey();
            properties[index].toolType = MotionEvent.TOOL_TYPE_FINGER;
            coords[index] = new MotionEvent.PointerCoords();
            coords[index].x = pointer.getValue()[0];
            coords[index].y = pointer.getValue()[1];
            coords[index].pressure = action == MotionEvent.ACTION_UP ? 0f : 1f;
            coords[index].size = 0.05f;
            if (pointer.getKey() == changed && action != MotionEvent.ACTION_DOWN && action != MotionEvent.ACTION_UP) {
                action |= index << MotionEvent.ACTION_POINTER_INDEX_SHIFT;
            }
            index++;
        }

        MotionEvent event = MotionEvent.obtain(
                downTime, SystemClock.uptimeMillis(), action, count, properties, coords,
                0, 0, 1f, 1f, deviceId, 0, InputDevice.SOURCE_TOUCHSCREEN, 0);
        injectInputEvent.invoke(inputManager, event, INJECT_MODE_ASYNC);
        event.recycle();
    }
}
