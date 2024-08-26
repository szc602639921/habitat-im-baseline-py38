# %%
import os
import gzip
import json
from multiprocessing import Pool, cpu_count
dataset_path =  "/home/users/shen/habitat-web-baselines/data/datasets/objectnav/objectnav_mp3d_70k/train/content"
dataset_name_list = os.listdir(dataset_path)
print(dataset_name_list)
# %%
dataset = "1pXnuDYAj8r.json.gz"
with gzip.open(os.path.join(dataset_path, dataset), 'r') as f:  # 4. gzip
    json_bytes = f.read()
json_str = json_bytes.decode('utf-8')  
data = json.loads(json_str)
# %%

# %%
# %%



import os
import gzip
import json
from multiprocessing import Pool, cpu_count


def process_dataset(dataset):
    dataset_path = "/home/users/shen/habitat-web-baselines/data/datasets/objectnav/objectnav_mp3d_70k/train/content"
    
    with gzip.open(os.path.join(dataset_path, dataset), 'r') as f:  # 4. gzip
        json_bytes = f.read()
    json_str = json_bytes.decode('utf-8')  
    data = json.loads(json_str)

    object_path = []
    for ep_id, ep in enumerate(data["episodes"]):
        try:
            if ep["scene_state"] != None:
                print(dataset)
                object_path.append(ep["scene_state"]['objects'][0]['object_template'])
        except:
            pass



    return object_path

if __name__ == "__main__":
    dataset_path =  "/home/users/shen/habitat-web-baselines/data/datasets/objectnav/objectnav_mp3d_70k/train/content"
    dataset_name_list = os.listdir(dataset_path)

    # Determine the number of processes to use
    num_processes = cpu_count()

    # Create a pool of workers
    with Pool(num_processes) as pool:
        # Map the datasets to the process_dataset function
        results = pool.map(process_dataset, dataset_name_list)


# %%
len(results)

# %%
import itertools
import numpy as np
concatenated_list = np.unique(list(itertools.chain(*results)))
# %%
sorted(concatenated_list)
# %%
np.unique(results[9])
# %%
