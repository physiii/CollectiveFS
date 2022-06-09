# Objective
The objective of CollectiveFS is to create a public file system where users can store personal files. I draw from protocols such as BitTorrent and BitCoin to create resilant distributed networks.

# Description
The cloud is a cluster of servers owned by a single entity. Typically, their motive is to collect payments directly or from 3rd parties which creates reliability and security risks. CollectiveFS serves to be a public alternative to privately owned cloud storage. Control is distributed among entities who choose to provide disc space in exchange for having their files on the network.

# Hidden in Plain Sight
File chunks are exchanged with untrusted peers but are encrypted. Symetric keys are used since the encryptor/decryptor are the same entity.

# Similar Projects
IPFS - Aims to replace IP based HTTP websites with content addressed ones hosted by p2p clusters. They introduce the concept of pinning where you can prioritize data. On CollectiveFS, each byte is as valued as any other byte on the network and parity can be configured so users can choose their desired level of fault tolerance against data erasures. IPFS also uses version control to track file history. On CollectiveFS, there is no version control although this can be implemented at the user level.

Hadoop - A distributed file system (HDFS) for big data. Used at companies like Facebook. Hadoop must be configured from the top down by a single entity where CollectiveFS is built from the bottom up by the individual nodes.

Syncthing - Synchronizes files over many nodes using p2p. Only synchronizes between nodes you own therefor is not public.


# Technologies
WebRTC  
Symmetric Enecryption (Fernet)  
Encoding (ReedSolomon)  
FUSE  

## Saving a file 
![Alt text](/images/CollectiveFS_save_file.png?raw=true "Saving files")


## Getting a file:
![Alt text](/images/CollectiveFS_get_file.png?raw=true "Saving files")
