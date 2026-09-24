package org.collectivefs.mobile;
/** Private app IPC for the native CollectiveFS node. Do not expose state over HTTP. */
public final class Node {
    static { System.loadLibrary("collectivefs"); }
    private Node() {}
    public static native String start(String config);
    public static native String state();
    public static native void stop();
}
