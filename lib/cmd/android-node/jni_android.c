#include <jni.h>
#include <stdlib.h>
extern char *CFSStart(char *);
extern char *CFSState(void);
extern void CFSStop(void);
JNIEXPORT jstring JNICALL Java_org_collectivefs_mobile_Node_start(JNIEnv *env,jclass cls,jstring json) {
 const char *raw=(*env)->GetStringUTFChars(env,json,0);if(!raw)return NULL;
 char *out=CFSStart((char *)raw);(*env)->ReleaseStringUTFChars(env,json,raw);
 jstring result=(*env)->NewStringUTF(env,out);free(out);return result;
}
JNIEXPORT jstring JNICALL Java_org_collectivefs_mobile_Node_state(JNIEnv *env,jclass cls) {char *out=CFSState();jstring result=(*env)->NewStringUTF(env,out);free(out);return result;}
JNIEXPORT void JNICALL Java_org_collectivefs_mobile_Node_stop(JNIEnv *env,jclass cls) {CFSStop();}
