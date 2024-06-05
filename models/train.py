import datetime
import json
import os
from matplotlib import pyplot as plt
import numpy as np
# from sklearn.base import accuracy_score
# from sklearn.linear_model import LogisticRegression
# from sklearn.manifold import TSNE
from sklearn.manifold import TSNE
import torch
import torch.nn as nn
import torch.nn.functional as F
from torch_geometric.nn import GraphSAGE
from sklearn.metrics import roc_auc_score, average_precision_score
from torch.optim.lr_scheduler import ReduceLROnPlateau
from torch_geometric.data import Data, InMemoryDataset, collate, Batch, Dataset
from torch_geometric.utils import negative_sampling
from torch_geometric.loader import DataLoader
import torch_geometric.nn as pyg_nn
import xml.etree.ElementTree as ET
import torch_geometric.transforms as T
from tqdm import tqdm
from preprocessing.GraphEmbedder import GraphEmbedder, MATHML_TAGS
from models.Graph.GraphDataset import GraphDataset
from models.Graph.GraphAutoEncoder import Encoder, GraphAutoencoder, GraphEncoder, SimpleEncoder
import random
from tensorboardX import SummaryWriter

from utils.plot import plot_loss_graph, plot_training_graphs
from utils.save import json_dump

def main():
    # Fix the randomness
    seed_value = 0
    random.seed(seed_value)
    np.random.seed(seed_value)
    torch.manual_seed(seed_value)
    torch.cuda.manual_seed(seed_value)
    torch.cuda.manual_seed_all(seed_value)  # if using multiple GPUs
    torch.backends.cudnn.deterministic = True
    torch.backends.cudnn.benchmark = False


    # Setup device
    device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    print('CUDA availability:', device)

    # Load dataset 
    print("Loading dataset...")
    dataset = GraphDataset(root='dataset/', equations_file='cleaned_formulas_katex.xml')
    # dataset.process(debug=False) # TODO: uncomment to reprocess if change of dataset or preprocessing!!!

    print(dataset.get_summary())
    data_size = len(dataset)
    train_data = dataset[:int(data_size * 0.8)]
    val_data = dataset[int(data_size * 0.8):int(data_size * 0.9)]
    test_data = dataset[int(data_size * 0.9):]

    in_channels = dataset.num_features
    model = pyg_nn.GAE(GraphSAGE(in_channels,hidden_channels=32,num_layers=4,out_channels=4))
    # model = pyg_nn.GAE(Encoder(in_channels, 16))
    # model = pyg_nn.GAE(GraphEncoder(in_channels, 16,8))
    optimizer = torch.optim.Adam(model.parameters(), lr=0.005)
    # criterion = torch.nn.MSELoss()

    # Early stopping
    patience = 5  # Number of epochs to wait for improvement before stopping
    patience_counter = 0
    scheduler = ReduceLROnPlateau(optimizer, mode='min', factor=0.01, patience=patience, threshold=0.01)

    writer = SummaryWriter("log/" + datetime.datetime.now().strftime("%Y%m%d-%H%M%S"),comment="training")

    # Put model to device
    model = model.to(device)

    batch_size = 128
    train_loader = DataLoader(train_data, batch_size=batch_size, shuffle=True, num_workers=8)
    val_loader = DataLoader(val_data, batch_size=batch_size, shuffle=False, num_workers=8)
    test_loader = DataLoader(test_data, batch_size=batch_size, shuffle=False, num_workers=8)

    # Training loop
    best_val_loss = float('inf')
    num_epochs = 25
    history = {"train_loss":[],"val_loss":[],"auc":[],"ap":[]}

    # Training loop
    for epoch in range(num_epochs):
        # Training phase
        avg_train_loss = train_one_epoch(model,optimizer,epoch,train_loader,device)
        
        # validation phase
        avg_val_loss, avg_auc, avg_ap = validate(model,val_loader,device)
        
        # Add to tensorboardX
        # writer.add_scalar("Loss/train", avg_train_loss, epoch)
        # writer.add_scalar("Loss/val", avg_val_loss, epoch)
        writer.add_scalars("Losses",{"train":avg_train_loss,"val":avg_val_loss},epoch)
        writer.add_scalar("Metrics/AUC", avg_auc, epoch)
        writer.add_scalar("Metrics/AP", avg_ap, epoch)

        history["train_loss"].append(avg_train_loss)
        history["val_loss"].append(avg_val_loss)
        history["auc"].append(avg_auc)
        history["ap"].append(avg_ap)

        print(f'Epoch: {epoch:03d}, Train Loss: {avg_train_loss:.4f}, Val Loss: {avg_val_loss:.4f}, AUC: {avg_auc:.4f}, AP: {avg_ap:.4f}')
        
        # Scheduler step
        scheduler.step(avg_val_loss)
        
        # Early stopping
        if avg_val_loss < best_val_loss:
            best_val_loss = avg_val_loss
            patience_counter = 0
        else:
            patience_counter += 1
        
        if patience_counter >= patience:
            print("Early stopping triggered")
            break
    
    # Create dir for saving model
    dir_path = "trained_models/exp_3"
    if not os.path.exists(dir_path):
        os.mkdir(dir_path)

    # Save the best model
    try:
        torch.save(model.state_dict(), f'{dir_path}/model_weights.pt')
    except Exception as e:
        print(f"Couldn't save the model because of {e}")

    # Plot graphs and save data
    try:
        plot_training_graphs(history,dir_path)
        json_dump(f'{dir_path}/history.json',history)
    except Exception as e:
        print(f"Couldn't save history and plot graphs because of {e}")

    # Load the best model (if early stopping was triggered)
    try:
        model.load_state_dict(torch.load(f'{dir_path}/model_weights.pt'))
    except Exception as e:
        print(f"Couldn't load the model because of {e}")

    test_model(model,test_loader,device)



def train_one_epoch(model,optimizer,epoch,train_loader,device, searching=False):
    # Training phase
    model.train()
    total_train_loss = 0

    # Setup tqdm or cancel if searching
    batches = tqdm(train_loader,desc=f"training epoch {epoch}",unit="batch")
    if searching:
        batches = train_loader
    
    for batch in batches:
        optimizer.zero_grad()
        batch = batch.to(device)

        # Generate negative edges
        pos_edge_index = batch.edge_index
        neg_edge_index = negative_sampling(
            edge_index=pos_edge_index, 
            num_nodes=batch.num_nodes, 
            num_neg_samples=pos_edge_index.size(1)
        ).to(device)
        
        z = model.encode(batch.x, batch.edge_index)
        loss = model.recon_loss(z, pos_edge_index, neg_edge_index)
        loss.backward()
        optimizer.step()
        total_train_loss += loss.item()
    
    avg_train_loss = total_train_loss / len(train_loader)
    return avg_train_loss

def validate(model,val_loader,device):
    model.eval()
    total_val_loss = 0
    total_auc = 0
    total_ap = 0

    with torch.no_grad():
        for batch in val_loader:
            batch = batch.to(device)

            # Generate negative edges
            pos_edge_index = batch.edge_index
            neg_edge_index = negative_sampling(
                edge_index=pos_edge_index, 
                num_nodes=batch.num_nodes, 
                num_neg_samples=pos_edge_index.size(1)
            ).to(device)
            
            z = model.encode(batch.x, batch.edge_index)
            loss = model.recon_loss(z, pos_edge_index, neg_edge_index)
            total_val_loss += loss.item()

            auc, ap = model.test(z, pos_edge_index, neg_edge_index)
            total_auc += auc
            total_ap += ap    

    avg_val_loss = total_val_loss / len(val_loader)
    avg_auc = total_auc / len(val_loader)
    avg_ap = total_ap / len(val_loader)

    return avg_val_loss, avg_auc, avg_ap


def test_model(model,test_loader,device):
    color_list = ["red", "orange", "green", "blue", "purple", "brown"]
    colors = []
    embs = []


    model.eval()
    with torch.no_grad():
        for batch in test_loader:
            batch = batch.to(device)


            # Generate negative edges
            pos_edge_index = batch.edge_index
            neg_edge_index = negative_sampling(
                edge_index=pos_edge_index, 
                num_nodes=batch.num_nodes, 
                num_neg_samples=pos_edge_index.size(1)
            ).to(device)
            
            z = model.encode(batch.x, pos_edge_index)
            # pred = model.decode()
            # colors += [color_list[y] for y in labels]

    xs, ys = zip(*TSNE().fit_transform(z.cpu().detach().numpy()))
    plt.scatter(xs, ys, color=colors)
    plt.show()

    

    
class EmbeddedDataLoader(DataLoader):
    def __init__(self, *args, embedder=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.embedder = embedder

    def __iter__(self):
        for batch in super().__iter__():
            # Apply the embedding function to the batch
            batch.x = self.embedder.embed_into_vector(batch.x)
            yield batch