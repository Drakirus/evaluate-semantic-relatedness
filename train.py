#!/usr/bin/env python
# -*- coding: utf-8 -*-

import pandas as pd
pd.set_option("display.width", 280)
pd.set_option('max_colwidth', 50)

from module.utils import force_symlink
from module.evaluate import evaluate
from module.models import RNNClassifier
import torch

device = torch.device('cuda:1' if torch.cuda.is_available() else 'cpu')
NUM_EPOCHS=3


### Board ###
from tensorboardX import SummaryWriter
writer = SummaryWriter()

#######################
#  Load the datasets  #
#######################


df_train = pd.read_csv("./sick_train/SICK_train.txt", sep="\t")
df_train = df_train.drop(['relatedness_score'], axis=1)

df_dev = pd.read_csv("./sick_trial/SICK_trial.txt", sep="\t")
df_dev = df_dev.drop(['relatedness_score'], axis=1)

df_test = pd.read_csv("./sick_test/SICK_test.txt", sep="\t")
df_test = df_test.drop(['relatedness_score'], axis=1)

from module.data import SickDataset
vocabulary_size = 1500

# Create the train dataset
sick_dataset_train = SickDataset(df_train, vocabulary_size)
#  print(sick_dataset_train.df.head())

dictionary_train = sick_dataset_train.getDictionary()

# Create the dev dataset
sick_dataset_dev = SickDataset(df_dev, vocabulary_size, dictionary_train)
# Create the test dataset
sick_dataset_test = SickDataset(df_test, vocabulary_size, dictionary_train)

print(pd.DataFrame(list(zip(sick_dataset_train.getRef(6)[-10:], sick_dataset_train[2][0]))).T)

#  sick_dataset_train.plotVocabularyCoverage()

#####################
#  Pretrained Embs  #
#####################
embeddings_size = 50

from module.pretrained_embeddings import load_embedding

print()

pretrained_emb_vec = load_embedding(
    sick_dataset_train,
    embeddings_size=embeddings_size,
    vocabulary_size=vocabulary_size)


# Debug
#  print(sick_dataset_train.dictionary.doc2idx(["the", "The"]))
#  print(sick_dataset_train.dictionary[18])
#  print(pretrained_emb_vec[18+1])
# Glove dim=50 word=the vector[:4] = 0.418 0.24968 -0.41242 0.1217

################
#  DataLoader  #
################

from torch.utils.data import DataLoader
from module.to_batch import pad_collate


BATCH_SIZE = 8

train_loader = DataLoader(dataset=sick_dataset_train,
                          batch_size=BATCH_SIZE, shuffle=True, collate_fn=pad_collate)

dev_loader = DataLoader(dataset=sick_dataset_dev,
                          batch_size=1, shuffle=False, collate_fn=pad_collate)

test_loader = DataLoader(dataset=sick_dataset_test,
                          batch_size=1, shuffle=False)

# Debug the padding
# display([ x for x in enumerate(train_loader)][0]) # has padding (sample of same size padded with 0)
# display([ x for x in enumerate(dev_loader)][0]) # no batch == no padding
print()

################
#  Classifier  #
################



# Add the unknown token (+1 to voc_size)
rnn = RNNClassifier(vocabulary_size+1, embeddings_size, 20, device=device)
rnn.to(device)
print(rnn)

# Set loss and optimizer function
# CrossEntropyLoss = LogSoftmax + NLLLoss
weights = [1-((sick_dataset_train.df['entailment_id'] == i).sum() / len(sick_dataset_train)) for i in range(3)]
class_weights = torch.FloatTensor(weights).to(device)
criterion = torch.nn.CrossEntropyLoss(weight=class_weights)

optimizer = torch.optim.Adam(rnn.parameters(), lr=0.001)


##########
#  Loop  #
##########

iter = 0
iter_batch = 0
best_accuracy_dev = 0.0

rnn.train()
import time
time_start = time.clock()

for epoch in range(NUM_EPOCHS):
    total_correct = 0
    total_target = 0
    train_loss_batches = 0
    train_loss_batches_count = 0
    for batch_idx, (data, target) in enumerate(train_loader):

        data = data.to(device)
        target = target.to(device)

        output = rnn(data)

        loss = criterion(output, target)


        train_loss_batches += loss.cpu().detach().numpy()
        train_loss_batches_count += 1

        rnn.zero_grad()
        loss.backward()
        optimizer.step()

        # Get the Accuracy
        _, predicted = torch.max(output.data, dim=1)
        correct = (predicted == target).sum().item()

        total_correct += correct
        total_target += target.size(0)

        if batch_idx % 200 == 0 or batch_idx % 200 == 1 or batch_idx == len(train_loader)-1:
            print('\rEpoch [{:3}/{}] | Step [{:5}/{} ({:3.0f}%)] | Loss {:.3f} | Accuracy {:.2f}%'.format(
                    epoch+1, NUM_EPOCHS,
                    batch_idx * len(data),
                    len(train_loader.dataset),
                    100. * batch_idx / len(train_loader),
                    loss.item(),
                    (total_correct / total_target) * 100), end=' ')

            writer.add_scalar('data/loss/_train_only', train_loss_batches / train_loss_batches_count, iter_batch)
            iter_batch += 1

        if False:
            break

    accuracy_dev, loss_dev = evaluate(rnn, dev_loader, criterion=criterion, whileTraining=True, device=device)
    print("@ Loss_dev {:.3f} | Accuracy_dev {:.2f}%".format(loss_dev, accuracy_dev))

    if best_accuracy_dev < accuracy_dev:
        best_accuracy_dev = accuracy_dev
        file = 'checkpoint.pth.' + str(epoch) + '.acc.' + str(round(accuracy_dev,2)) + '.tar'
        torch.save({
            'epoch': epoch+1,
            'model_state_dict': rnn.state_dict(),
            'loss': loss,
            }, file)
        force_symlink(file, 'checkpoint.pth.best.tar')
    writer.add_scalars('data/loss/evol', {'train': train_loss_batches / train_loss_batches_count,

                                         'dev': loss_dev}, iter)
    iter += 1




time_elapsed = (time.clock() - time_start)
print("Learning finished!\n - in", round(time_elapsed, 2), "s")


##########
#  Eval  #
##########

checkpoint = torch.load('checkpoint.pth.best.tar')
rnn.load_state_dict(checkpoint['model_state_dict'])
print("=> loaded checkpoint epoch {}"
      .format(checkpoint['epoch']))


evaluate(rnn, dev_loader, writer=writer, device=device)


# evaluate(rnn, test_loader)
